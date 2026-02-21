"""
Status Agent.

A project status management agent that uses predefined pipelines to execute
data operations deterministically, then lets the LLM synthesize results.

This avoids relying on the LLM's (often limited) ability to select and chain
multiple tools, while still leveraging LLM intelligence for:
- Classifying user intent
- Extracting structured data from freeform text
- Synthesizing human-readable responses from tool results

Architecture mirrors WebSearchAgent: deterministic pipeline + LLM synthesis.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Intent classification categories
INTENT_CREATE = "create"
INTENT_CONFIRM_CREATE = "confirm_create"
INTENT_QUERY = "query"
INTENT_UPDATE = "update"
INTENT_CHAT = "chat"

# Well-known data document names for the busibox-projects app
STATUS_DOC_PROJECTS = "busibox-projects-projects"
STATUS_DOC_TASKS = "busibox-projects-tasks"
STATUS_DOC_UPDATES = "busibox-projects-updates"
STATUS_SOURCE_APP = "busibox-projects"

# Synthesis prompt -- the LLM only needs to produce a nice response from tool results
STATUS_SYNTHESIS_PROMPT = """You are a project status assistant. Given tool results and user context, create a clear, well-organized response.

Guidelines:
- Start with a brief summary of what was done or found
- Use **bold** for project names and key terms
- Use bullet points for lists
- Include relevant metrics (record counts, progress %)
- Be concise and actionable
- If records were created, list what was created with a checkmark
- If querying data, format results in a readable way"""


# Extraction prompt -- used to parse user text into structured records
EXTRACTION_SYSTEM_PROMPT = """You are a data extraction specialist. Extract structured project and task data from the user's text.

You MUST respond with valid JSON only. No other text.

Output format:
{
  "projects": [
    {
      "name": "Project Name",
      "description": "Brief description",
      "status": "on-track",
      "progress": 0,
      "owner": "",
      "tags": []
    }
  ],
  "tasks": [
    {
      "project_name": "Parent Project Name",
      "title": "Task title",
      "description": "Brief description",
      "status": "todo",
      "priority": "medium",
      "assignee": ""
    }
  ]
}

Rules:
- status for projects: "on-track", "at-risk", "off-track", "completed", "paused"
- status for tasks: "todo", "in-progress", "blocked", "done"
- priority for tasks: "low", "medium", "high", "urgent"
- Set progress based on context (e.g. "5 hrs of 20 hrs" = 25)
- If hours are mentioned, include them in the description
- Extract ALL projects and tasks from the text
- Group tasks under their parent project
- If no clear parent project, use the most relevant one"""


# Intent classification prompt
INTENT_SYSTEM_PROMPT = """Classify the user's intent into exactly one category. Respond with ONLY the category name.

Categories:
- create: User wants to create new projects, tasks, or records
- query: User wants to view, list, or check status of existing data
- update: User wants to modify existing projects or tasks
- chat: General conversation, questions, or anything else

Examples:
- "create these projects" -> create
- "use your tools to create project entries" -> create
- "what's the status of project X?" -> query
- "list all projects" -> query
- "mark task Y as done" -> update
- "change project X to at-risk" -> update
- "hello" -> chat
- "what can you do?" -> chat"""


def _ensure_openai_env():
    """Ensure OpenAI environment is configured for LiteLLM."""
    from busibox_common.llm import ensure_openai_env
    settings = get_settings()
    ensure_openai_env(
        base_url=str(settings.litellm_base_url),
        api_key=settings.litellm_api_key,
    )


def _is_status_app_document(doc: Dict[str, Any]) -> bool:
    """
    Return True if a data document belongs to the busibox-projects status app.

    This prevents status agents from writing into similarly named docs owned by
    other apps (e.g. busibox-appbuilder-projects), which can cause schema 400s.
    """
    name = str(doc.get("name", "")).strip()
    if not name:
        return False
    lower_name = name.lower()
    source_app = str(doc.get("sourceApp") or doc.get("source_app") or "").strip().lower()
    if source_app == STATUS_SOURCE_APP:
        return True
    if name in {STATUS_DOC_PROJECTS, STATUS_DOC_TASKS, STATUS_DOC_UPDATES}:
        return True
    return lower_name.startswith(f"{STATUS_SOURCE_APP}-")


class StatusAssistantAgent(BaseStreamingAgent):
    """
    A streaming status management agent that:
    1. Classifies user intent (create/query/update/chat)
    2. Executes a predefined tool pipeline based on intent
    3. Synthesizes results into a clear response

    Uses ToolStrategy.SEQUENTIAL with dynamic pipeline steps,
    following the same pattern as WebSearchAgent.
    """

    def __init__(self):
        config = AgentConfig(
            name="status-assistant-agent",
            display_name="Project Status Assistant",
            instructions=STATUS_SYNTHESIS_PROMPT,
            tools=[
                "list_data_documents",
                "create_data_document",
                "query_data",
                "insert_records",
                "update_records",
                "search_users",
            ],
            # Must use RUN_MAX_ITERATIONS (not RUN_ONCE) because our pipeline
            # uses dynamic chaining via process_tool_result(). RUN_ONCE breaks
            # the loop after the first step, preventing chained steps from executing.
            execution_mode=ExecutionMode.RUN_MAX_ITERATIONS,
            max_iterations=20,
            tool_strategy=ToolStrategy.SEQUENTIAL,
        )
        super().__init__(config)

        # State for the current execution
        self._intent: str = INTENT_CHAT
        self._doc_ids: Dict[str, str] = {}  # name -> document_id
        self._extracted_data: Dict[str, Any] = {}  # parsed from user text
        self._query_results: Dict[str, Any] = {}
        self._user_id_cache: Dict[str, str] = {}  # normalized name -> user_id
        self._bootstrapping: bool = False  # True when creating initial data documents
        self._awaiting_create_check: bool = False  # True when query_data is checking for existing projects before create

        # Lazy-init LLM agents for classification and extraction
        self._classifier: Optional[Agent] = None
        self._extractor: Optional[Agent] = None

    def _get_classifier(self) -> Agent:
        """Get or create the intent classifier (uses fast model)."""
        if self._classifier is None:
            _ensure_openai_env()
            settings = get_settings()
            model = OpenAIChatModel(
                model_name=settings.fast_model,
                provider="openai",
            )
            self._classifier = Agent(
                model=model,
                system_prompt=INTENT_SYSTEM_PROMPT,
            )
        return self._classifier

    def _get_extractor(self) -> Agent:
        """Get or create the data extractor (uses agent model for better extraction)."""
        if self._extractor is None:
            _ensure_openai_env()
            settings = get_settings()
            model = OpenAIChatModel(
                model_name=settings.default_model,
                provider="openai",
            )
            self._extractor = Agent(
                model=model,
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
            )
        return self._extractor

    def _detect_confirm_create_from_history(self, query: str, context: AgentContext) -> bool:
        """
        Check if the conversation history indicates the user is confirming
        a previously asked "create or update?" question.

        Pattern:
        - Last assistant message asked about existing projects / confirmation
        - Current user message is affirmative
        """
        if not context.recent_messages:
            return False

        # Find the last assistant message
        last_assistant_content = ""
        for msg in reversed(context.recent_messages):
            if msg.get("role") == "assistant":
                last_assistant_content = msg.get("content", "").lower()
                break

        if not last_assistant_content:
            return False

        # Check if the assistant asked a confirmation question about projects
        confirmation_phrases = [
            "did you want to",
            "would you like to create",
            "shall i create",
            "create new ones",
            "update these",
            "existing project",
            "already have",
            "found existing",
            "found matching",
        ]
        asked_confirmation = any(
            phrase in last_assistant_content for phrase in confirmation_phrases
        )
        if not asked_confirmation:
            return False

        # Check if the user's current message is affirmative
        query_lower = query.lower().strip()
        affirmative_patterns = [
            "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
            "go ahead", "do it", "create", "create new", "create them",
            "create new ones", "make new", "proceed", "confirm",
            "please create", "yes create", "yes please",
        ]
        return any(pattern in query_lower for pattern in affirmative_patterns)

    async def _classify_intent(self, query: str, context: AgentContext) -> str:
        """
        Classify the user's intent using a lightweight LLM call.

        Falls back to keyword-based classification if LLM fails.
        """
        # FIRST: Check if this is a confirmation of a previous create prompt
        if self._detect_confirm_create_from_history(query, context):
            logger.info("Detected INTENT_CONFIRM_CREATE from conversation history")
            return INTENT_CONFIRM_CREATE

        # Quick keyword checks for obvious cases
        query_lower = query.lower()
        create_keywords = ["create", "add", "make", "build", "insert", "new project",
                           "new task", "set up", "initialize"]
        update_keywords = ["update", "change", "mark as", "set status", "modify",
                           "mark done", "mark complete", "mark blocked"]
        query_keywords = ["status", "list", "show", "what", "how many", "progress",
                          "check", "report", "overview", "summary"]

        # Strong keyword matches (saves an LLM call)
        for kw in create_keywords:
            if kw in query_lower:
                return INTENT_CREATE
        for kw in update_keywords:
            if kw in query_lower:
                return INTENT_UPDATE
        for kw in query_keywords:
            if kw in query_lower:
                return INTENT_QUERY

        # Fall back to LLM classification
        try:
            classifier = self._get_classifier()
            result = await classifier.run(query)
            intent = str(result.output).strip().lower()
            if intent in (INTENT_CREATE, INTENT_QUERY, INTENT_UPDATE, INTENT_CHAT):
                return intent
        except Exception as e:
            logger.warning(f"Intent classification failed, defaulting to chat: {e}")

        return INTENT_CHAT

    @staticmethod
    def _fix_json(text: str) -> str:
        """
        Attempt to fix common LLM JSON errors:
        - Trailing commas before ] or }
        - Single quotes instead of double quotes
        - Unquoted keys
        - JavaScript-style comments
        """
        # Remove single-line comments (// ...)
        text = re.sub(r'//[^\n]*', '', text)
        # Remove multi-line comments (/* ... */)
        text = re.sub(r'/\*[\s\S]*?\*/', '', text)
        # Remove trailing commas before } or ]
        text = re.sub(r',\s*([}\]])', r'\1', text)
        # Replace single quotes with double quotes (crude but works for simple cases)
        # Only do this if the text doesn't already parse
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
        # Try replacing single quotes
        fixed = re.sub(r"'", '"', text)
        return fixed

    @staticmethod
    def _extract_json_from_text(text: str) -> str:
        """Extract JSON object from LLM output, handling code blocks and noise."""
        # Handle markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if json_match:
            return json_match.group(1)
        # Try to find raw JSON object (greedy to get the outermost braces)
        brace_match = re.search(r'\{[\s\S]*\}', text)
        if brace_match:
            return brace_match.group(0)
        return text

    async def _extract_records(self, query: str, context: AgentContext) -> Dict[str, Any]:
        """
        Extract structured project/task data from user text using LLM.

        Returns dict with 'projects' and 'tasks' lists.
        Includes robust JSON parsing with error correction for local LLMs.
        """
        # Build context with conversation history
        extract_prompt = query
        if context.recent_messages:
            # Include recent messages for context (the data might be in earlier messages)
            history_parts = []
            for msg in context.recent_messages[-6:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if role == "user" and content:
                    history_parts.append(content)
            if history_parts:
                extract_prompt = "\n\n".join(history_parts) + "\n\n" + query

        for attempt in range(2):
            try:
                extractor = self._get_extractor()
                prompt = f"Extract projects and tasks from this text:\n\n{extract_prompt}"
                if attempt > 0:
                    prompt += "\n\nIMPORTANT: You MUST output valid JSON. No trailing commas. No comments."
                result = await extractor.run(prompt)
                output = str(result.output).strip()

                # Extract JSON from response text
                output = self._extract_json_from_text(output)

                # Try parsing directly first
                try:
                    parsed = json.loads(output)
                except json.JSONDecodeError:
                    # Try fixing common JSON errors
                    fixed = self._fix_json(output)
                    parsed = json.loads(fixed)

                projects = parsed.get("projects", [])
                tasks = parsed.get("tasks", [])
                logger.info(f"Extracted {len(projects)} projects and {len(tasks)} tasks (attempt {attempt + 1})")
                return {"projects": projects, "tasks": tasks}

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    continue  # Retry with stricter prompt
                logger.error(f"Record extraction failed after {attempt + 1} attempts: {e}")
            except Exception as e:
                logger.error(f"Record extraction failed: {e}")
                break

        return {"projects": [], "tasks": []}

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        Build the initial pipeline.

        Always starts with list_data_documents to discover document IDs.
        The actual pipeline varies by intent and is built dynamically
        in process_tool_result after classification and list_data_documents.
        """
        # Reset state for new execution
        self._doc_ids = {}
        self._extracted_data = {}
        self._query_results = {}
        self._awaiting_create_check = False

        # Always start by listing data documents to get IDs
        return [
            PipelineStep(
                tool="list_data_documents",
                args={"limit": 20},
            )
        ]

    async def process_tool_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Process tool results and chain next pipeline steps dynamically.

        After list_data_documents: classify intent and build remaining pipeline.
        After create_data_document: track new document IDs during bootstrap.
        After query_data: store results for synthesis.
        After insert/update_records: store results for synthesis.
        """
        if step.tool == "list_data_documents":
            return await self._handle_list_docs_result(result, context)
        elif step.tool == "create_data_document":
            return await self._handle_create_doc_result(step, result, context)
        elif step.tool == "query_data":
            return await self._handle_query_result(step, result, context)
        elif step.tool == "insert_records":
            return self._handle_insert_result(step, result, context)
        elif step.tool == "update_records":
            return self._handle_update_result(step, result, context)
        elif step.tool == "search_users":
            return self._handle_user_search_result(step, result, context)
        return []

    def _get_original_query(self, context: AgentContext) -> str:
        """Extract the original user query from context."""
        if context.recent_messages:
            for msg in reversed(context.recent_messages):
                if msg.get("role") == "user":
                    return msg.get("content", "")
        return context.metadata.get("_original_query", "")

    async def _handle_list_docs_result(
        self,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        After listing documents, classify intent and build the remaining pipeline.
        """
        # Extract document IDs by name -- prefer exact well-known names first
        if hasattr(result, 'documents') and result.documents:
            candidate_docs = [doc for doc in result.documents if _is_status_app_document(doc)]
            for doc in candidate_docs:
                name = doc.get("name", "")
                doc_id = doc.get("id", "")
                if not name or not doc_id:
                    continue
                self._doc_ids[name] = doc_id
                # Primary match: exact well-known names
                if name == STATUS_DOC_PROJECTS:
                    self._doc_ids["projects"] = doc_id
                elif name == STATUS_DOC_TASKS:
                    self._doc_ids["tasks"] = doc_id
                elif name == STATUS_DOC_UPDATES:
                    self._doc_ids["updates"] = doc_id

            # Fallback: substring match if well-known names not found
            if "projects" not in self._doc_ids or "tasks" not in self._doc_ids:
                for doc in candidate_docs:
                    name = doc.get("name", "").lower()
                    doc_id = doc.get("id", "")
                    if "projects" not in self._doc_ids and "project" in name:
                        self._doc_ids["projects"] = doc_id
                    elif "tasks" not in self._doc_ids and "task" in name:
                        self._doc_ids["tasks"] = doc_id
                    elif "updates" not in self._doc_ids and "update" in name:
                        self._doc_ids["updates"] = doc_id

        logger.info(f"Found documents: {self._doc_ids}")

        if not self._doc_ids:
            logger.info("No data documents found -- bootstrapping busibox-projects documents")
            return self._build_bootstrap_pipeline()

        original_query = self._get_original_query(context)

        self._intent = await self._classify_intent(original_query, context)
        logger.info(f"Classified intent: {self._intent} for query: {original_query[:80]}...")

        # Build pipeline based on intent
        if self._intent in (INTENT_CREATE, INTENT_CONFIRM_CREATE):
            return await self._build_create_pipeline(original_query, context)
        elif self._intent == INTENT_QUERY:
            return self._build_query_pipeline(original_query, context)
        elif self._intent == INTENT_UPDATE:
            return self._build_update_pipeline(original_query, context)
        else:
            # Chat intent -- no tools needed, just synthesize
            return []

    def _build_bootstrap_pipeline(self) -> List[PipelineStep]:
        """
        Create the three required data documents when none exist.

        The busibox-projects app uses three well-known documents:
        - busibox-projects-projects
        - busibox-projects-tasks
        - busibox-projects-updates

        After all three are created, the pipeline continues with intent handling.
        """
        self._bootstrapping = True
        return [
            PipelineStep(
                tool="create_data_document",
                args={
                    "name": STATUS_DOC_PROJECTS,
                    "schema": {
                        "fields": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string"},
                            "progress": {"type": "number"},
                            "owner": {"type": "string"},
                            "tags": {"type": "array"},
                            "checkpointProgress": {"type": "number"},
                            "nextCheckpoint": {"type": "string"},
                            "checkpointDate": {"type": "string"},
                            "team": {"type": "array"},
                        }
                    },
                    "visibility": "personal",
                    "source_app": "busibox-projects",
                },
            ),
            PipelineStep(
                tool="create_data_document",
                args={
                    "name": STATUS_DOC_TASKS,
                    "schema": {
                        "fields": {
                            "projectId": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string"},
                            "assignee": {"type": "string"},
                            "assigneeId": {"type": "string"},
                            "priority": {"type": "string"},
                            "dueDate": {"type": "string"},
                            "order": {"type": "number"},
                        }
                    },
                    "visibility": "personal",
                    "source_app": "busibox-projects",
                },
            ),
            PipelineStep(
                tool="create_data_document",
                args={
                    "name": STATUS_DOC_UPDATES,
                    "schema": {
                        "fields": {
                            "projectId": {"type": "string"},
                            "content": {"type": "string"},
                            "author": {"type": "string"},
                            "tasksCompleted": {"type": "array"},
                            "tasksAdded": {"type": "array"},
                            "previousStatus": {"type": "string"},
                            "newStatus": {"type": "string"},
                        }
                    },
                    "visibility": "personal",
                    "source_app": "busibox-projects",
                },
            ),
        ]

    async def _handle_create_doc_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Track newly created document IDs during bootstrap.

        After the last document is created, classify intent and build the main pipeline.
        """
        doc_name = step.args.get("name", "")
        # Extract document ID from result
        doc_id = ""
        if hasattr(result, 'document_id'):
            doc_id = result.document_id
        elif hasattr(result, 'id'):
            doc_id = result.id
        elif isinstance(result, dict):
            doc_id = result.get("document_id", result.get("id", ""))

        if doc_name and doc_id:
            self._doc_ids[doc_name] = doc_id
            # Map to simplified keys
            if "project" in doc_name.lower():
                self._doc_ids["projects"] = doc_id
            elif "task" in doc_name.lower():
                self._doc_ids["tasks"] = doc_id
            elif "update" in doc_name.lower():
                self._doc_ids["updates"] = doc_id
            logger.info(f"Created document: {doc_name} -> {doc_id}")

        # Check if all three bootstrap documents are now created
        if self._bootstrapping and all(
            k in self._doc_ids for k in ("projects", "tasks", "updates")
        ):
            self._bootstrapping = False
            logger.info(f"Bootstrap complete. Documents: {self._doc_ids}")

            # Now classify intent and build the main pipeline
            original_query = ""
            if context.recent_messages:
                for msg in reversed(context.recent_messages):
                    if msg.get("role") == "user":
                        original_query = msg.get("content", "")
                        break
            if not original_query:
                original_query = context.metadata.get("_original_query", "")

            self._intent = await self._classify_intent(original_query, context)
            logger.info(f"Post-bootstrap intent: {self._intent}")

            if self._intent == INTENT_CREATE:
                return await self._build_create_pipeline(original_query, context)
            elif self._intent == INTENT_QUERY:
                return self._build_query_pipeline(original_query, context)
            elif self._intent == INTENT_UPDATE:
                return self._build_update_pipeline(original_query, context)

        return []

    async def _build_create_pipeline(
        self,
        query: str,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Build pipeline for creating projects and tasks.

        For INTENT_CONFIRM_CREATE (user confirmed after being asked about matches):
          1. Extract structured data from conversation history
          2. Go straight to insert (skip duplicate check)

        For INTENT_CREATE (first request):
          1. Extract structured data from user text via LLM
          2. Query existing projects to check for duplicates
          3. In process_tool_result, decide: insert vs ask-confirm
        """
        self._extracted_data = await self._extract_records(query, context)

        projects = self._extracted_data.get("projects", [])
        tasks = self._extracted_data.get("tasks", [])

        projects_doc_id = self._doc_ids.get("projects")
        tasks_doc_id = self._doc_ids.get("tasks")

        if tasks and tasks_doc_id:
            # Store tasks for later processing (after projects are inserted)
            self._extracted_data["_pending_tasks"] = tasks

        if not projects or not projects_doc_id:
            return []

        # If user already confirmed creation, skip duplicate check
        if self._intent == INTENT_CONFIRM_CREATE:
            return self._build_insert_steps()

        # Query existing projects to check for matches (include description for LLM matching)
        self._awaiting_create_check = True
        return [PipelineStep(
            tool="query_data",
            args={
                "document_id": projects_doc_id,
                "select": ["id", "name", "description", "status", "progress"],
                "limit": 50,
            },
        )]

    def _build_insert_steps(self) -> List[PipelineStep]:
        """Build the insert_records steps for projects (and tasks follow via chaining)."""
        projects = self._extracted_data.get("projects", [])
        projects_doc_id = self._doc_ids.get("projects")

        if not projects or not projects_doc_id:
            return []

        records = []
        for p in projects:
            records.append({
                "name": p.get("name", "Unnamed Project"),
                "description": p.get("description", ""),
                "status": p.get("status", "on-track"),
                "progress": p.get("progress", 0),
                "owner": p.get("owner", ""),
                "tags": p.get("tags", []),
            })
        return [PipelineStep(
            tool="insert_records",
            args={
                "document_id": projects_doc_id,
                "records": records,
            },
        )]

    def _build_query_pipeline(
        self,
        query: str,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """Build pipeline for querying project/task data."""
        steps = []
        query_lower = query.lower()

        projects_doc_id = self._doc_ids.get("projects")
        tasks_doc_id = self._doc_ids.get("tasks")

        # Always query projects for an overview
        if projects_doc_id:
            steps.append(PipelineStep(
                tool="query_data",
                args={
                    "document_id": projects_doc_id,
                    "select": ["id", "name", "status", "progress", "owner"],
                    "limit": 20,
                },
            ))

        # Query tasks if relevant
        if tasks_doc_id and any(kw in query_lower for kw in
                                ["task", "todo", "blocked", "done", "in-progress", "all"]):
            steps.append(PipelineStep(
                tool="query_data",
                args={
                    "document_id": tasks_doc_id,
                    "select": ["id", "projectId", "title", "status", "priority", "assignee"],
                    "limit": 20,
                },
            ))

        return steps

    def _build_update_pipeline(
        self,
        query: str,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Build pipeline for updating existing data.

        First queries existing data to find what to update,
        then the actual update is built in process_tool_result.
        """
        steps = []
        projects_doc_id = self._doc_ids.get("projects")
        tasks_doc_id = self._doc_ids.get("tasks")

        # Query current state first
        if projects_doc_id:
            steps.append(PipelineStep(
                tool="query_data",
                args={
                    "document_id": projects_doc_id,
                    "select": ["id", "name", "status", "progress"],
                    "limit": 20,
                },
            ))

        if tasks_doc_id:
            steps.append(PipelineStep(
                tool="query_data",
                args={
                    "document_id": tasks_doc_id,
                    "select": ["id", "projectId", "title", "status", "priority"],
                    "limit": 20,
                },
            ))

        return steps

    async def _handle_query_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """Store query results for synthesis -- or chain inserts for create flow."""
        doc_id = step.args.get("document_id", "")

        doc_type = "unknown"
        for name, did in self._doc_ids.items():
            if did == doc_id:
                doc_type = name
                break

        if hasattr(result, 'records'):
            self._query_results[doc_type] = {
                "records": result.records,
                "total": getattr(result, 'total', len(result.records)),
            }

        if self._awaiting_create_check:
            self._awaiting_create_check = False
            return await self._handle_create_query_result(result, context)

        return []

    async def _handle_create_query_result(
        self,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Compare extracted projects against existing ones using substring match
        first, then an LLM similarity classifier for ambiguous cases.

        Outcomes per project:
        - High-confidence match (>0.8): auto-update existing project
        - Medium-confidence match (0.5-0.8): ask user to confirm
        - No/low match: create new project
        """
        existing_records = []
        if hasattr(result, 'records') and result.records:
            existing_records = result.records

        extracted_projects = self._extracted_data.get("projects", [])
        if not extracted_projects:
            return []

        existing_by_name = {
            rec.get("name", "").lower(): rec
            for rec in existing_records
            if rec.get("name")
        }

        auto_update: List[Dict[str, Any]] = []
        confirm_matches: List[Dict[str, Any]] = []
        unmatched: List[Dict[str, Any]] = []

        for proj in extracted_projects:
            proj_name = proj.get("name", "").lower()
            proj_desc = proj.get("description", "").lower()

            # Phase 1: exact / substring match (high confidence)
            substring_match = None
            for existing_name, existing_rec in existing_by_name.items():
                if proj_name == existing_name:
                    substring_match = existing_rec
                    break
                if proj_name in existing_name or existing_name in proj_name:
                    substring_match = existing_rec
                    break

            if substring_match:
                auto_update.append({"extracted": proj, "existing": substring_match})
                continue

            # Phase 2: LLM similarity check for non-obvious matches
            if existing_records:
                llm_match = await self._llm_match_project(proj, existing_records)
                if llm_match:
                    confidence = llm_match.get("confidence", 0)
                    if confidence >= 0.8:
                        auto_update.append({
                            "extracted": proj,
                            "existing": llm_match["existing"],
                        })
                    elif confidence >= 0.5:
                        confirm_matches.append({
                            "extracted": proj,
                            "existing": llm_match["existing"],
                            "confidence": confidence,
                        })
                    else:
                        unmatched.append(proj)
                else:
                    unmatched.append(proj)
            else:
                unmatched.append(proj)

        # Build combined response
        if auto_update:
            self._extracted_data["_auto_update"] = auto_update
            logger.info(
                "Auto-updating %d projects with high-confidence matches",
                len(auto_update),
            )

        if confirm_matches:
            self._extracted_data["_matches"] = confirm_matches
            self._extracted_data["_unmatched"] = unmatched
            logger.info(
                "Found %d projects needing confirmation, %d new",
                len(confirm_matches),
                len(unmatched),
            )
            # Don't insert yet — synthesis will ask user to confirm medium matches
            return self._build_auto_update_steps()

        if auto_update and not unmatched:
            return self._build_auto_update_steps()

        if auto_update and unmatched:
            # Insert unmatched as new, update matched ones
            self._extracted_data["projects"] = unmatched
            steps = self._build_auto_update_steps()
            steps.extend(self._build_insert_steps())
            return steps

        # No matches at all
        logger.info("No matching projects found -- proceeding with insert")
        return self._build_insert_steps()

    async def _llm_match_project(
        self,
        extracted: Dict[str, Any],
        existing_records: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Use the fast LLM to determine if an extracted project matches any
        existing project. Returns the best match with a confidence score,
        or None if no match.
        """
        try:
            _ensure_openai_env()
            settings = get_settings()
            model = OpenAIChatModel(
                model_name=settings.fast_model or settings.default_model,
                provider="openai",
            )

            existing_summaries = []
            for i, rec in enumerate(existing_records[:20]):
                name = rec.get("name", "Unnamed")
                desc = rec.get("description", "")
                status = rec.get("status", "")
                existing_summaries.append(
                    f"{i}: \"{name}\" - {desc} (status: {status})"
                )

            prompt = (
                f"Compare this new project against existing projects and determine "
                f"if it matches any of them.\n\n"
                f"New project: \"{extracted.get('name', '')}\" - "
                f"{extracted.get('description', '')}\n\n"
                f"Existing projects:\n"
                + "\n".join(existing_summaries)
                + "\n\nRespond with ONLY valid JSON: "
                f'{{ "match_index": <index or -1>, "confidence": <0.0-1.0> }}'
            )

            agent = Agent(model=model, system_prompt="You match projects by semantic similarity. Respond with JSON only.")
            result = await agent.run(prompt)
            text = str(result.output).strip()

            # Extract JSON from response
            match = re.search(r'\{[^}]+\}', text)
            if not match:
                return None

            data = json.loads(match.group())
            idx = data.get("match_index", -1)
            confidence = float(data.get("confidence", 0))

            if idx < 0 or idx >= len(existing_records) or confidence < 0.3:
                return None

            return {
                "existing": existing_records[idx],
                "confidence": confidence,
            }
        except Exception as e:
            logger.warning("LLM project matching failed: %s", e)
            return None

    def _build_auto_update_steps(self) -> List[PipelineStep]:
        """Build update_records steps for auto-matched projects."""
        auto_updates = self._extracted_data.get("_auto_update", [])
        if not auto_updates:
            return []

        projects_doc_id = self._doc_ids.get("projects")
        if not projects_doc_id:
            return []

        steps = []
        for match in auto_updates:
            extracted = match["extracted"]
            existing = match["existing"]
            existing_id = existing.get("id")
            if not existing_id:
                continue

            updates: Dict[str, Any] = {}
            for field in ("description", "status", "progress", "owner", "tags"):
                val = extracted.get(field)
                if val and val != existing.get(field):
                    updates[field] = val

            if not updates:
                continue

            steps.append(PipelineStep(
                tool="update_records",
                args={
                    "document_id": projects_doc_id,
                    "record_ids": [existing_id],
                    "updates": updates,
                },
            ))

            # Map extracted project name to existing ID for task linking
            extracted_name = extracted.get("name", "").strip().lower()
            if extracted_name:
                self._extracted_data.setdefault("_project_id_map", {})[extracted_name] = existing_id

        # Also handle pending tasks for auto-updated projects
        pending_tasks = self._extracted_data.get("_pending_tasks", [])
        if pending_tasks and self._doc_ids.get("tasks"):
            id_map = self._extracted_data.get("_project_id_map", {})
            if id_map:
                # Re-map tasks to use existing project IDs
                for task in pending_tasks:
                    pname = task.get("project_name", "").strip().lower()
                    if pname in id_map:
                        task["_resolved_project_id"] = id_map[pname]

        return steps

    def _handle_insert_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        After inserting projects, insert pending tasks linked to the new project IDs.
        """
        pending_tasks = self._extracted_data.get("_pending_tasks", [])
        tasks_doc_id = self._doc_ids.get("tasks")

        if not pending_tasks or not tasks_doc_id:
            return []

        # Check if this was the projects insert (not a tasks insert)
        projects_doc_id = self._doc_ids.get("projects")
        if step.args.get("document_id") != projects_doc_id:
            return []  # This was the tasks insert, nothing more to do

        def _norm(value: Any) -> str:
            return str(value or "").strip().lower()

        # Start with any IDs resolved during auto-update matching
        project_name_to_id: Dict[str, str] = dict(
            self._extracted_data.get("_project_id_map", {})
        )

        # Merge IDs from the insert result
        if hasattr(result, 'record_ids') and result.record_ids:
            projects = self._extracted_data.get("projects", [])
            for i, rid in enumerate(result.record_ids):
                if i < len(projects):
                    name_key = _norm(projects[i].get("name", ""))
                    if name_key:
                        project_name_to_id[name_key] = rid

        # Fallback for API responses that don't include record_ids
        if not project_name_to_id:
            extracted_projects = self._extracted_data.get("projects", [])
            known_projects = (self._query_results.get("projects", {}) or {}).get("records", [])
            if len(extracted_projects) == 1 and len(known_projects) == 1:
                single_name = _norm(extracted_projects[0].get("name", ""))
                single_id = str(known_projects[0].get("id", ""))
                if single_name and single_id:
                    project_name_to_id[single_name] = single_id

        # Build task records with projectId references
        task_records = []
        skipped_tasks = []
        assignee_names_to_resolve: List[str] = []

        for task in pending_tasks:
            # Use pre-resolved ID from auto-update flow if available
            project_id = task.pop("_resolved_project_id", "")

            if not project_id:
                project_name = task.get("project_name", "")
                normalized_project_name = _norm(project_name)
                project_id = project_name_to_id.get(normalized_project_name, "")

                if not project_id and normalized_project_name:
                    for pname, pid in project_name_to_id.items():
                        if normalized_project_name in pname or pname in normalized_project_name:
                            project_id = pid
                            break

                if not project_id and len(project_name_to_id) == 1:
                    project_id = next(iter(project_name_to_id.values()))

            if not project_id:
                skipped_tasks.append(task.get("title", "Untitled Task"))
                continue

            assignee = task.get("assignee", "").strip()
            if assignee and assignee.lower() not in self._user_id_cache:
                assignee_names_to_resolve.append(assignee)

            task_records.append({
                "projectId": project_id,
                "title": task.get("title", "Untitled Task"),
                "description": task.get("description", ""),
                "status": task.get("status", "todo"),
                "priority": task.get("priority", "medium"),
                "assignee": assignee,
            })

        if skipped_tasks:
            logger.warning(
                "Skipped %d tasks without project mapping: %s",
                len(skipped_tasks),
                skipped_tasks[:10],
            )

        self._extracted_data["_pending_tasks"] = []

        if not task_records:
            return []

        # Deduplicate assignee names to search
        unique_names = list(dict.fromkeys(n for n in assignee_names_to_resolve if n))

        if unique_names:
            # Defer task insert until user searches complete
            self._extracted_data["_deferred_task_records"] = task_records
            self._extracted_data["_pending_user_searches"] = unique_names[1:]
            return [PipelineStep(tool="search_users", args={"query": unique_names[0]})]

        # No assignees to resolve — insert immediately
        return [PipelineStep(
            tool="insert_records",
            args={"document_id": tasks_doc_id, "records": task_records},
        )]

    def _handle_user_search_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Cache user search results and, once all pending searches are resolved,
        proceed with the task insert that was deferred.
        """
        search_query = step.args.get("query", "").strip().lower()
        if hasattr(result, "users") and result.users:
            best = result.users[0]
            user_id = best.get("id", "")
            if user_id:
                self._user_id_cache[search_query] = user_id
                # Also cache by individual name parts for fuzzy matching
                for field in ("display_name", "first_name", "last_name", "email"):
                    val = best.get(field, "")
                    if val:
                        self._user_id_cache[val.strip().lower()] = user_id

        # Check if there are still pending user searches
        pending_searches = self._extracted_data.get("_pending_user_searches", [])
        if pending_searches:
            next_name = pending_searches.pop(0)
            return [PipelineStep(tool="search_users", args={"query": next_name})]

        # All user searches done — proceed with the deferred task insert
        return self._build_task_insert_from_cache()

    def _build_task_insert_from_cache(self) -> List[PipelineStep]:
        """Build task insert steps using the resolved user ID cache."""
        deferred_tasks = self._extracted_data.get("_deferred_task_records", [])
        tasks_doc_id = self._doc_ids.get("tasks")
        if not deferred_tasks or not tasks_doc_id:
            return []

        for task in deferred_tasks:
            assignee_name = task.get("assignee", "").strip()
            if assignee_name:
                user_id = self._user_id_cache.get(assignee_name.lower(), "")
                if user_id:
                    task["assigneeId"] = user_id

        self._extracted_data["_deferred_task_records"] = []
        return [PipelineStep(
            tool="insert_records",
            args={"document_id": tasks_doc_id, "records": deferred_tasks},
        )]

    def _handle_update_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        After update_records completes, check if there are pending tasks
        that need to be inserted (from the auto-update create flow).
        """
        pending_tasks = self._extracted_data.get("_pending_tasks", [])
        tasks_doc_id = self._doc_ids.get("tasks")

        if not pending_tasks or not tasks_doc_id:
            return []

        # Build task records with resolved project IDs
        id_map = self._extracted_data.get("_project_id_map", {})
        task_records = []
        assignee_names_to_resolve: List[str] = []

        def _norm(value: Any) -> str:
            return str(value or "").strip().lower()

        for task in pending_tasks:
            project_id = task.pop("_resolved_project_id", "")
            if not project_id:
                pname = _norm(task.get("project_name", ""))
                project_id = id_map.get(pname, "")
                if not project_id and len(id_map) == 1:
                    project_id = next(iter(id_map.values()))

            if not project_id:
                continue

            assignee = task.get("assignee", "").strip()
            if assignee and assignee.lower() not in self._user_id_cache:
                assignee_names_to_resolve.append(assignee)

            task_records.append({
                "projectId": project_id,
                "title": task.get("title", "Untitled Task"),
                "description": task.get("description", ""),
                "status": task.get("status", "todo"),
                "priority": task.get("priority", "medium"),
                "assignee": assignee,
            })

        self._extracted_data["_pending_tasks"] = []

        if not task_records:
            return []

        unique_names = list(dict.fromkeys(n for n in assignee_names_to_resolve if n))
        if unique_names:
            self._extracted_data["_deferred_task_records"] = task_records
            self._extracted_data["_pending_user_searches"] = unique_names[1:]
            return [PipelineStep(tool="search_users", args={"query": unique_names[0]})]

        return [PipelineStep(
            tool="insert_records",
            args={"document_id": tasks_doc_id, "records": task_records},
        )]

    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """Build context for the synthesis LLM from tool results."""
        parts = []

        # Add conversation history for context
        if context.compressed_history_summary:
            parts.append(f"## Previous Conversation Summary\n{context.compressed_history_summary}\n")

        if context.recent_messages:
            parts.append("## Recent Conversation")
            for msg in context.recent_messages[-4:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if role == "user":
                    parts.append(f"User: {content}")
                elif role == "assistant":
                    parts.append(f"Assistant: {content}")
            parts.append("")

        parts.append(f"## Current Query\n{query}\n")
        parts.append(f"## Intent\n{self._intent}\n")

        # Add tool results
        if self._intent in (INTENT_CREATE, INTENT_CONFIRM_CREATE):
            confirm_matches = self._extracted_data.get("_matches", [])
            auto_updates = self._extracted_data.get("_auto_update", [])
            unmatched = self._extracted_data.get("_unmatched", [])

            if confirm_matches and self._intent == INTENT_CREATE:
                parts.append("## Existing Projects Found")
                parts.append(
                    "The user asked to create projects, but similar ones already exist. "
                    "Ask the user whether they want to UPDATE the existing projects or "
                    "CREATE NEW entries. Be specific about which projects matched."
                )
                parts.append("\n### Matching Projects (need confirmation)")
                for m in confirm_matches:
                    ext = m["extracted"]
                    ex = m["existing"]
                    conf = m.get("confidence", "?")
                    parts.append(
                        f"- Requested: **{ext.get('name', '?')}** ↔ "
                        f"Existing: **{ex.get('name', '?')}** "
                        f"(status: {ex.get('status', '?')}, "
                        f"progress: {ex.get('progress', 0)}%, "
                        f"similarity: {conf})"
                    )
                if auto_updates:
                    parts.append(f"\n### Auto-Updated Projects ({len(auto_updates)})")
                    for m in auto_updates:
                        ext = m["extracted"]
                        ex = m["existing"]
                        parts.append(
                            f"- ✅ Updated **{ex.get('name', '?')}** with new data from "
                            f"**{ext.get('name', '?')}**"
                        )
                if unmatched:
                    parts.append(f"\n### New Projects (no match found, {len(unmatched)})")
                    for p in unmatched:
                        parts.append(f"- **{p.get('name', 'Unnamed')}**: {p.get('description', '')}")
                parts.append(
                    "\nAsk the user: would they like to update the matching projects, "
                    "or create new entries for them?"
                )
            else:
                parts.append("## Actions Taken")

                if auto_updates:
                    parts.append(f"\n### Projects Updated ({len(auto_updates)})")
                    for m in auto_updates:
                        ext = m["extracted"]
                        ex = m["existing"]
                        parts.append(
                            f"- ✅ Updated **{ex.get('name', '?')}** with new data"
                        )

                for tool_name, result in context.tool_results.items():
                    if tool_name == "list_data_documents":
                        continue
                    if tool_name == "insert_records":
                        if hasattr(result, 'success') and result.success:
                            count = getattr(result, 'count', 0)
                            parts.append(f"- Inserted **{count}** records successfully")
                        elif hasattr(result, 'error'):
                            parts.append(f"- Insert failed: {result.error}")

                projects = self._extracted_data.get("projects", [])
                tasks = self._extracted_data.get("tasks", [])
                if projects:
                    parts.append(f"\n### Projects Created ({len(projects)})")
                    for p in projects:
                        parts.append(f"- **{p.get('name', 'Unnamed')}**: {p.get('description', 'No description')}")
                if tasks:
                    parts.append(f"\n### Tasks Created ({len(tasks)})")
                    for t in tasks:
                        parts.append(f"- **{t.get('title', 'Untitled')}** ({t.get('project_name', 'unassigned')})")

        elif self._intent == INTENT_QUERY:
            parts.append("## Data")
            for doc_type, data in self._query_results.items():
                records = data.get("records", [])
                total = data.get("total", len(records))
                parts.append(f"\n### {doc_type} ({total} total)")
                for rec in records[:20]:
                    # Format record as key-value pairs
                    fields = [f"{k}: {v}" for k, v in rec.items() if k != "id" and v]
                    parts.append(f"- {', '.join(fields)}")

        elif self._intent == INTENT_UPDATE:
            parts.append("## Current Data")
            for doc_type, data in self._query_results.items():
                records = data.get("records", [])
                parts.append(f"\n### {doc_type}")
                for rec in records[:10]:
                    fields = [f"{k}: {v}" for k, v in rec.items() if k != "id" and v]
                    parts.append(f"- {', '.join(fields)}")
            parts.append("\n## Updates Applied")
            for tool_name, result in context.tool_results.items():
                if tool_name == "update_records":
                    if hasattr(result, 'success') and result.success:
                        parts.append(f"- Updated {getattr(result, 'count', 0)} records")
                    elif hasattr(result, 'error'):
                        parts.append(f"- Update failed: {result.error}")

        else:
            # Chat -- just provide whatever context we have
            if context.tool_results:
                parts.append("## Available Data")
                for tool_name, result in context.tool_results.items():
                    if tool_name == "list_data_documents":
                        if hasattr(result, 'documents'):
                            parts.append(f"Documents available: {len(result.documents)}")

        parts.append(
            "\nPlease provide a clear, helpful response to the user based on "
            "the above context and results."
        )
        return "\n".join(parts)

    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """Build a fallback response if synthesis fails."""
        if self._intent in (INTENT_CREATE, INTENT_CONFIRM_CREATE):
            confirm_matches = self._extracted_data.get("_matches", [])
            auto_updates = self._extracted_data.get("_auto_update", [])
            if confirm_matches and self._intent == INTENT_CREATE:
                parts = ["I found existing projects that match your request:\n"]
                for m in confirm_matches:
                    ext = m["extracted"]
                    ex = m["existing"]
                    parts.append(
                        f"- **{ext.get('name', '?')}** matches existing "
                        f"**{ex.get('name', '?')}** "
                        f"({ex.get('status', '?')}, {ex.get('progress', 0)}%)"
                    )
                if auto_updates:
                    parts.append(f"\nI also auto-updated {len(auto_updates)} matching projects.")
                parts.append(
                    "\nWould you like to update the matching projects "
                    "or create new entries?"
                )
                return "\n".join(parts)

            parts = ["Here's what I did:\n"]
            if auto_updates:
                parts.append(f"**Updated {len(auto_updates)} existing projects:**")
                for m in auto_updates:
                    parts.append(f"- ✅ {m['existing'].get('name', 'Unnamed')}")

            projects = self._extracted_data.get("projects", [])
            tasks = self._extracted_data.get("tasks", [])
            if projects:
                parts.append(f"\n**Created {len(projects)} new projects:**")
                for p in projects:
                    parts.append(f"- {p.get('name', 'Unnamed')}")
            if tasks:
                parts.append(f"\n**Created {len(tasks)} tasks:**")
                for t in tasks:
                    parts.append(f"- {t.get('title', 'Untitled')}")
            return "\n".join(parts)

        elif self._intent == INTENT_QUERY:
            parts = ["Here's what I found:\n"]
            for doc_type, data in self._query_results.items():
                records = data.get("records", [])
                parts.append(f"\n**{doc_type}** ({len(records)} records):")
                for rec in records[:5]:
                    name = rec.get("name") or rec.get("title", "Unknown")
                    status = rec.get("status", "")
                    parts.append(f"- {name} ({status})")
            return "\n".join(parts)

        return "I processed your request. Please check the data for details."

    def _format_tool_result_message(self, tool_name: str, result: Any) -> str:
        """Format human-readable streaming messages for tool results."""
        if tool_name == "list_data_documents":
            if hasattr(result, 'documents'):
                count = len(result.documents)
                if count == 0:
                    return "No data stores found -- will create them"
                return f"Found **{count} data stores**"
            return "Checked data stores"

        if tool_name == "create_data_document":
            if hasattr(result, 'name'):
                return f"Created data store **{result.name}**"
            return "Created data store"

        if tool_name == "query_data":
            if hasattr(result, 'records'):
                return f"Found **{len(result.records)} records**"
            return "Query completed"

        if tool_name == "insert_records":
            if hasattr(result, 'success') and result.success:
                count = getattr(result, 'count', 0)
                return f"Created **{count} records** successfully"
            elif hasattr(result, 'error'):
                return f"Insert failed: {result.error}"
            return "Insert completed"

        if tool_name == "update_records":
            if hasattr(result, 'success') and result.success:
                count = getattr(result, 'count', 0)
                return f"Updated **{count} records** successfully"
            elif hasattr(result, 'error'):
                return f"Update failed: {result.error}"
            return "Update completed"

        return super()._format_tool_result_message(tool_name, result)

    async def run_with_streaming(
        self,
        query: str,
        stream,
        cancel,
        context: Optional[dict] = None,
    ) -> str:
        """
        Override to store the original query and reset per-request state.
        """
        # Reset per-call state (singleton is reused across requests)
        self._intent = INTENT_CHAT
        self._doc_ids = {}
        self._extracted_data = {}
        self._query_results = {}
        self._user_id_cache = {}
        self._bootstrapping = False
        self._awaiting_create_check = False

        if context is None:
            context = {}
        if "metadata" not in context or context["metadata"] is None:
            context["metadata"] = {}
        context["metadata"]["_original_query"] = query

        return await super().run_with_streaming(query, stream, cancel, context)


# Singleton instances
status_assistant_agent = StatusAssistantAgent()


# Update extraction prompt -- used to parse user's status update into actionable changes
UPDATE_EXTRACTION_PROMPT = """You are a status update parser. Given the user's message and existing project/task data, extract what changes should be made.

You MUST respond with valid JSON only. No other text.

Output format:
{
  "target_project": "Name of the project being updated (must match an existing project)",
  "project_updates": {
    "status": "on-track",
    "progress": 50,
    "description": "Updated description if provided"
  },
  "task_updates": [
    {
      "title": "Existing task title",
      "status": "done",
      "notes": "Optional update note"
    }
  ],
  "new_tasks": [
    {
      "title": "New task title",
      "description": "Brief description",
      "status": "todo",
      "priority": "medium"
    }
  ],
  "status_summary": "One-sentence summary of what changed"
}

Rules:
- target_project MUST match an existing project name from the provided data
- Only include fields in project_updates that are actually changing
- task_updates: for tasks that already exist and need status/field changes
- new_tasks: for brand new tasks the user mentions
- status for projects: "on-track", "at-risk", "off-track", "completed", "paused"
- status for tasks: "todo", "in-progress", "blocked", "done"
- If the user says they completed something, mark relevant tasks as "done"
- If the user mentions new work, add it as new_tasks
- Calculate progress from context (e.g., "3 of 5 tasks done" = 60)
- If no project_updates are needed, use an empty object {}
- If no task_updates, use an empty list []
- If no new_tasks, use an empty list []"""


class StatusUpdateAgent(BaseStreamingAgent):
    """
    Status update agent that records changes to projects and tasks.

    Pipeline:
    1. list_data_documents -> discover doc IDs
    2. query_data (projects + tasks) -> get current state
    3. LLM extraction -> parse user's update into structured changes
    4. update_records / insert_records -> persist changes
    5. insert_records (status update log) -> record the update
    """

    def __init__(self):
        config = AgentConfig(
            name="status-update-agent",
            display_name="Status Update Assistant",
            instructions="""You are a concise status update assistant. Given project and task data with the user's update, summarize what was changed.

Guidelines:
- Be brief and efficient
- List changes as bullet points
- Show before/after for status changes
- Suggest next steps if appropriate""",
            tools=[
                "list_data_documents",
                "create_data_document",
                "query_data",
                "insert_records",
                "update_records",
            ],
            # Must use RUN_MAX_ITERATIONS for dynamic pipeline chaining
            execution_mode=ExecutionMode.RUN_MAX_ITERATIONS,
            max_iterations=20,
            tool_strategy=ToolStrategy.SEQUENTIAL,
        )
        super().__init__(config)
        self._doc_ids: Dict[str, str] = {}
        self._existing_projects: List[Dict[str, Any]] = []
        self._existing_tasks: List[Dict[str, Any]] = []
        self._update_data: Dict[str, Any] = {}
        self._queries_done: int = 0
        self._expected_queries: int = 0
        self._write_steps_built: bool = False

        # Lazy-init LLM agent for update extraction
        self._update_extractor: Optional[Agent] = None

    def _get_update_extractor(self) -> Agent:
        """Get or create the update extractor (uses agent model)."""
        if self._update_extractor is None:
            _ensure_openai_env()
            settings = get_settings()
            model = OpenAIChatModel(
                model_name=settings.default_model,
                provider="openai",
            )
            self._update_extractor = Agent(
                model=model,
                system_prompt=UPDATE_EXTRACTION_PROMPT,
            )
        return self._update_extractor

    async def _extract_update(self, query: str, context: AgentContext) -> Dict[str, Any]:
        """Extract structured update data from the user's message using LLM."""
        # Build context showing existing data so the LLM can match project/task names
        existing_context = "## Existing Projects\n"
        for p in self._existing_projects:
            existing_context += f"- {p.get('name', '?')} (status: {p.get('status', '?')}, progress: {p.get('progress', 0)}%)\n"

        existing_context += "\n## Existing Tasks\n"
        for t in self._existing_tasks:
            existing_context += f"- {t.get('title', '?')} (status: {t.get('status', '?')}, project: {t.get('projectId', '?')})\n"

        # Include conversation history
        history = ""
        if context.recent_messages:
            for msg in context.recent_messages[-4:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if content:
                    history += f"{role}: {content}\n"

        prompt = f"{existing_context}\n## Conversation History\n{history}\n## Current Update\n{query}\n\nExtract the update actions as JSON."

        for attempt in range(2):
            try:
                extractor = self._get_update_extractor()
                p = prompt
                if attempt > 0:
                    p += "\n\nIMPORTANT: You MUST output valid JSON. No trailing commas. No comments."
                result = await extractor.run(p)
                output = str(result.output).strip()

                # Extract JSON from response text
                output = StatusAssistantAgent._extract_json_from_text(output)

                # Try parsing directly first
                try:
                    parsed = json.loads(output)
                except json.JSONDecodeError:
                    fixed = StatusAssistantAgent._fix_json(output)
                    parsed = json.loads(fixed)

                logger.info(f"Extracted update: target={parsed.get('target_project')}, "
                            f"project_updates={bool(parsed.get('project_updates'))}, "
                            f"task_updates={len(parsed.get('task_updates', []))}, "
                            f"new_tasks={len(parsed.get('new_tasks', []))}")
                return parsed

            except json.JSONDecodeError as e:
                logger.warning(f"Update JSON parse failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    continue
                logger.error(f"Update extraction failed after {attempt + 1} attempts: {e}")
            except Exception as e:
                logger.error(f"Update extraction failed: {e}")
                break

        return {}

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        # Reset state
        self._doc_ids = {}
        self._existing_projects = []
        self._existing_tasks = []
        self._update_data = {}
        self._queries_done = 0
        self._expected_queries = 0
        self._write_steps_built = False
        return [
            PipelineStep(
                tool="list_data_documents",
                args={"limit": 20},
            )
        ]

    async def process_tool_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        if step.tool == "list_data_documents":
            return self._handle_list_docs(result)
        elif step.tool == "query_data":
            return await self._handle_query(step, result, context)
        elif step.tool in ("update_records", "insert_records"):
            # Write steps complete -- nothing more to chain
            return []
        return []

    def _handle_list_docs(self, result: Any) -> List[PipelineStep]:
        """Extract doc IDs and queue query steps."""
        if hasattr(result, 'documents') and result.documents:
            candidate_docs = [doc for doc in result.documents if _is_status_app_document(doc)]
            for doc in candidate_docs:
                name = doc.get("name", "")
                doc_id = doc.get("id", "")
                if not name or not doc_id:
                    continue
                # Exact match first
                if name == STATUS_DOC_PROJECTS:
                    self._doc_ids["projects"] = doc_id
                elif name == STATUS_DOC_TASKS:
                    self._doc_ids["tasks"] = doc_id
                elif name == STATUS_DOC_UPDATES:
                    self._doc_ids["updates"] = doc_id
                # Fallback substring
                elif "projects" not in self._doc_ids and "project" in name.lower():
                    self._doc_ids["projects"] = doc_id
                elif "tasks" not in self._doc_ids and "task" in name.lower():
                    self._doc_ids["tasks"] = doc_id
                elif "updates" not in self._doc_ids and "update" in name.lower():
                    self._doc_ids["updates"] = doc_id

        steps = []
        if self._doc_ids.get("projects"):
            steps.append(PipelineStep(
                tool="query_data",
                args={
                    "document_id": self._doc_ids["projects"],
                    "select": ["id", "name", "status", "progress", "description"],
                    "limit": 20,
                },
            ))
        if self._doc_ids.get("tasks"):
            steps.append(PipelineStep(
                tool="query_data",
                args={
                    "document_id": self._doc_ids["tasks"],
                    "select": ["id", "projectId", "title", "status", "priority", "description"],
                    "limit": 50,
                },
            ))
        self._expected_queries = len(steps)
        return steps

    async def _handle_query(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """Collect query results; after all queries, extract update and build write steps."""
        doc_id = step.args.get("document_id", "")

        if hasattr(result, 'records') and result.records:
            if doc_id == self._doc_ids.get("projects"):
                self._existing_projects = result.records
            elif doc_id == self._doc_ids.get("tasks"):
                self._existing_tasks = result.records

        self._queries_done += 1

        # Wait until all queries have completed before building write steps
        if self._queries_done < self._expected_queries:
            return []

        # Don't build write steps more than once
        if self._write_steps_built:
            return []
        self._write_steps_built = True

        # Extract the user's update using LLM
        original_query = ""
        if context.recent_messages:
            for msg in reversed(context.recent_messages):
                if msg.get("role") == "user":
                    original_query = msg.get("content", "")
                    break
        if not original_query:
            original_query = context.metadata.get("_original_query", "")

        self._update_data = await self._extract_update(original_query, context)
        if not self._update_data:
            logger.warning("No update data extracted -- nothing to write")
            return []

        return self._build_write_steps()

    def _build_write_steps(self) -> List[PipelineStep]:
        """Build insert/update pipeline steps from extracted update data."""
        steps: List[PipelineStep] = []

        target_project_name = self._update_data.get("target_project", "")
        project_updates = self._update_data.get("project_updates", {})
        task_updates = self._update_data.get("task_updates", [])
        new_tasks = self._update_data.get("new_tasks", [])
        status_summary = self._update_data.get("status_summary", "")

        # Find the target project ID
        target_project_id = ""
        target_project_old_status = ""
        for p in self._existing_projects:
            if p.get("name", "").lower() == target_project_name.lower():
                target_project_id = p.get("id", "")
                target_project_old_status = p.get("status", "")
                break
            # Fuzzy match
            if (target_project_name.lower() in p.get("name", "").lower()
                    or p.get("name", "").lower() in target_project_name.lower()):
                target_project_id = p.get("id", "")
                target_project_old_status = p.get("status", "")
                break

        projects_doc = self._doc_ids.get("projects")
        tasks_doc = self._doc_ids.get("tasks")
        updates_doc = self._doc_ids.get("updates")

        # 1. Update project fields if needed
        if project_updates and target_project_id and projects_doc:
            # Only include fields that are present and non-empty
            clean_updates = {k: v for k, v in project_updates.items() if v is not None and v != ""}
            if clean_updates:
                steps.append(PipelineStep(
                    tool="update_records",
                    args={
                        "document_id": projects_doc,
                        "updates": clean_updates,
                        "where": {"field": "id", "op": "eq", "value": target_project_id},
                    },
                ))

        # 2. Update existing tasks
        for task_update in task_updates:
            task_title = task_update.get("title", "")
            if not task_title:
                continue
            # Find the task ID
            task_id = ""
            for t in self._existing_tasks:
                if t.get("title", "").lower() == task_title.lower():
                    task_id = t.get("id", "")
                    break
                if (task_title.lower() in t.get("title", "").lower()
                        or t.get("title", "").lower() in task_title.lower()):
                    task_id = t.get("id", "")
                    break

            if task_id and tasks_doc:
                updates = {k: v for k, v in task_update.items()
                           if k not in ("title", "notes") and v is not None and v != ""}
                if updates:
                    steps.append(PipelineStep(
                        tool="update_records",
                        args={
                            "document_id": tasks_doc,
                            "updates": updates,
                            "where": {"field": "id", "op": "eq", "value": task_id},
                        },
                    ))

        # 3. Insert new tasks
        if new_tasks and tasks_doc:
            records = []
            for task in new_tasks:
                records.append({
                    "projectId": target_project_id,
                    "title": task.get("title", "Untitled"),
                    "description": task.get("description", ""),
                    "status": task.get("status", "todo"),
                    "priority": task.get("priority", "medium"),
                    "assignee": task.get("assignee", ""),
                })
            if records:
                steps.append(PipelineStep(
                    tool="insert_records",
                    args={
                        "document_id": tasks_doc,
                        "records": records,
                    },
                ))

        # 4. Record the status update log entry
        if updates_doc and (project_updates or task_updates or new_tasks):
            completed_tasks = [t.get("title", "") for t in task_updates
                               if t.get("status") == "done"]
            added_tasks = [t.get("title", "") for t in new_tasks]

            steps.append(PipelineStep(
                tool="insert_records",
                args={
                    "document_id": updates_doc,
                    "records": [{
                        "projectId": target_project_id,
                        "content": status_summary or "Status update",
                        "author": "",  # Will be filled by RLS/context
                        "tasksCompleted": completed_tasks,
                        "tasksAdded": added_tasks,
                        "previousStatus": target_project_old_status,
                        "newStatus": project_updates.get("status", target_project_old_status),
                    }],
                },
            ))

        logger.info(f"Built {len(steps)} write steps for status update")
        return steps

    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        parts = [f"## User's Update\n{query}\n"]

        if context.recent_messages:
            parts.append("## Recent Conversation")
            for msg in context.recent_messages[-4:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                parts.append(f"{role.title()}: {content}")
            parts.append("")

        # Show what was changed
        if self._update_data:
            parts.append("## Changes Applied")
            target = self._update_data.get("target_project", "Unknown")
            parts.append(f"**Target project:** {target}\n")

            project_updates = self._update_data.get("project_updates", {})
            if project_updates:
                parts.append("### Project Updates")
                for k, v in project_updates.items():
                    parts.append(f"- **{k}**: {v}")

            task_updates = self._update_data.get("task_updates", [])
            if task_updates:
                parts.append(f"\n### Tasks Updated ({len(task_updates)})")
                for t in task_updates:
                    parts.append(f"- **{t.get('title', '?')}** → {t.get('status', '?')}")

            new_tasks = self._update_data.get("new_tasks", [])
            if new_tasks:
                parts.append(f"\n### New Tasks Added ({len(new_tasks)})")
                for t in new_tasks:
                    parts.append(f"- **{t.get('title', '?')}** ({t.get('status', 'todo')})")

            summary = self._update_data.get("status_summary", "")
            if summary:
                parts.append(f"\n### Summary\n{summary}")
        else:
            parts.append("## Current Data")
            for tool_name, result in context.tool_results.items():
                if tool_name == "query_data" and hasattr(result, 'records'):
                    for rec in result.records[:10]:
                        fields = [f"{k}: {v}" for k, v in rec.items() if k != "id" and v]
                        parts.append(f"- {', '.join(fields)}")

        # Show tool execution results
        write_results = []
        for tool_name, result in context.tool_results.items():
            if tool_name == "update_records":
                if hasattr(result, 'success') and result.success:
                    write_results.append(f"- Updated **{getattr(result, 'count', 0)}** records")
            elif tool_name == "insert_records":
                if hasattr(result, 'success') and result.success:
                    write_results.append(f"- Inserted **{getattr(result, 'count', 0)}** records")
        if write_results:
            parts.append("\n## Write Results")
            parts.extend(write_results)

        parts.append(
            "\nProvide a concise summary of the status update. "
            "Show what changed and suggest next steps."
        )
        return "\n".join(parts)

    async def run_with_streaming(
        self,
        query: str,
        stream,
        cancel,
        context: Optional[dict] = None,
    ) -> str:
        """Store original query in metadata and reset per-request state."""
        self._doc_ids = {}
        self._existing_projects = []
        self._existing_tasks = []
        self._update_data = {}
        self._queries_done = 0
        self._expected_queries = 0
        self._write_steps_built = False

        if context is None:
            context = {}
        if "metadata" not in context or context["metadata"] is None:
            context["metadata"] = {}
        context["metadata"]["_original_query"] = query
        return await super().run_with_streaming(query, stream, cancel, context)


status_update_agent = StatusUpdateAgent()
