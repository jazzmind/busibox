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

# Well-known data document names for the status-report app
STATUS_DOC_PROJECTS = "status-report-projects"
STATUS_DOC_TASKS = "status-report-tasks"
STATUS_DOC_UPDATES = "status-report-updates"

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

    async def _extract_records(self, query: str, context: AgentContext) -> Dict[str, Any]:
        """
        Extract structured project/task data from user text using LLM.

        Returns dict with 'projects' and 'tasks' lists.
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

        try:
            extractor = self._get_extractor()
            result = await extractor.run(
                f"Extract projects and tasks from this text:\n\n{extract_prompt}"
            )
            output = str(result.output).strip()

            # Try to parse JSON from the response
            # Handle potential markdown code blocks
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', output)
            if json_match:
                output = json_match.group(1)
            else:
                # Try to find raw JSON object
                brace_match = re.search(r'\{[\s\S]*\}', output)
                if brace_match:
                    output = brace_match.group(0)

            parsed = json.loads(output)
            projects = parsed.get("projects", [])
            tasks = parsed.get("tasks", [])
            logger.info(f"Extracted {len(projects)} projects and {len(tasks)} tasks")
            return {"projects": projects, "tasks": tasks}

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Record extraction failed: {e}")
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
            return self._handle_query_result(step, result, context)
        elif step.tool == "insert_records":
            return self._handle_insert_result(step, result, context)
        elif step.tool == "update_records":
            return self._handle_update_result(step, result, context)
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
            for doc in result.documents:
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
                for doc in result.documents:
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
            logger.info("No data documents found -- bootstrapping status-report documents")
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

        The status-report app uses three well-known documents:
        - status-report-projects
        - status-report-tasks
        - status-report-updates

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
                    "source_app": "status-report",
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
                            "priority": {"type": "string"},
                            "dueDate": {"type": "string"},
                            "order": {"type": "number"},
                        }
                    },
                    "visibility": "personal",
                    "source_app": "status-report",
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
                    "source_app": "status-report",
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

        # Otherwise, query existing projects to check for matches first
        self._awaiting_create_check = True
        return [PipelineStep(
            tool="query_data",
            args={
                "document_id": projects_doc_id,
                "select": ["id", "name", "status", "progress"],
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

    def _handle_query_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """Store query results for synthesis -- or chain inserts for create flow."""
        doc_id = step.args.get("document_id", "")

        # Determine which document this is
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

        # If this was a "check existing before create" query, handle matching
        if self._awaiting_create_check:
            self._awaiting_create_check = False
            return self._handle_create_query_result(result, context)

        # For update intent, after all queries are done, we would need to
        # build update steps. For now, we let the synthesis handle it.
        return []

    def _handle_create_query_result(
        self,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """
        Compare extracted projects against existing ones.

        If matches found: store match details and return [] (synthesis will ask user).
        If no matches: proceed with insert steps.
        """
        existing_records = []
        if hasattr(result, 'records') and result.records:
            existing_records = result.records

        extracted_projects = self._extracted_data.get("projects", [])
        if not extracted_projects:
            return []

        # Compare extracted project names against existing ones (fuzzy match)
        matches = []
        unmatched = []
        existing_names = {
            rec.get("name", "").lower(): rec
            for rec in existing_records
            if rec.get("name")
        }

        for proj in extracted_projects:
            proj_name = proj.get("name", "").lower()
            matched = False
            for existing_name, existing_rec in existing_names.items():
                # Exact or substring match
                if (proj_name == existing_name
                        or proj_name in existing_name
                        or existing_name in proj_name):
                    matches.append({
                        "extracted": proj,
                        "existing": existing_rec,
                    })
                    matched = True
                    break
            if not matched:
                unmatched.append(proj)

        if matches:
            # Store match details for synthesis to build a confirmation question
            self._extracted_data["_matches"] = matches
            self._extracted_data["_unmatched"] = unmatched
            logger.info(
                f"Found {len(matches)} matching projects, "
                f"{len(unmatched)} new projects -- asking user to confirm"
            )
            # Return empty -- synthesis will ask the user
            return []

        # No matches at all -- safe to proceed with inserts
        logger.info("No matching projects found -- proceeding with insert")
        return self._build_insert_steps()

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

        # Map project names to IDs from the insert result
        project_name_to_id = {}
        if hasattr(result, 'record_ids') and result.record_ids:
            projects = self._extracted_data.get("projects", [])
            for i, rid in enumerate(result.record_ids):
                if i < len(projects):
                    project_name_to_id[projects[i].get("name", "")] = rid

        # Build task records with projectId references
        task_records = []
        for task in pending_tasks:
            project_name = task.get("project_name", "")
            project_id = project_name_to_id.get(project_name, "")

            # If no exact match, try fuzzy matching
            if not project_id and project_name:
                for pname, pid in project_name_to_id.items():
                    if project_name.lower() in pname.lower() or pname.lower() in project_name.lower():
                        project_id = pid
                        break

            task_records.append({
                "projectId": project_id,
                "title": task.get("title", "Untitled Task"),
                "description": task.get("description", ""),
                "status": task.get("status", "todo"),
                "priority": task.get("priority", "medium"),
                "assignee": task.get("assignee", ""),
            })

        # Clear pending tasks to avoid re-insertion
        self._extracted_data["_pending_tasks"] = []

        if task_records:
            return [PipelineStep(
                tool="insert_records",
                args={
                    "document_id": tasks_doc_id,
                    "records": task_records,
                },
            )]

        return []

    def _handle_update_result(
        self,
        step: PipelineStep,
        result: Any,
        context: AgentContext,
    ) -> List[PipelineStep]:
        """Store update results for synthesis."""
        # Update results are captured by context.tool_results automatically
        return []

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
            # Check if we're asking the user to confirm (matches were found)
            matches = self._extracted_data.get("_matches", [])
            unmatched = self._extracted_data.get("_unmatched", [])

            if matches and self._intent == INTENT_CREATE:
                # Matches found -- ask the user what to do
                parts.append("## Existing Projects Found")
                parts.append(
                    "The user asked to create projects, but similar ones already exist. "
                    "Ask the user whether they want to UPDATE the existing projects or "
                    "CREATE NEW entries. Be specific about which projects matched."
                )
                parts.append("\n### Matching Projects")
                for m in matches:
                    ext = m["extracted"]
                    ex = m["existing"]
                    parts.append(
                        f"- Requested: **{ext.get('name', '?')}** ↔ "
                        f"Existing: **{ex.get('name', '?')}** "
                        f"(status: {ex.get('status', '?')}, "
                        f"progress: {ex.get('progress', 0)}%)"
                    )
                if unmatched:
                    parts.append(f"\n### New Projects (no match found, {len(unmatched)})")
                    for p in unmatched:
                        parts.append(f"- **{p.get('name', 'Unnamed')}**: {p.get('description', '')}")
                parts.append(
                    "\nAsk the user: would they like to update the existing projects, "
                    "or create new entries for everything?"
                )
            else:
                # Normal create flow -- show what was created
                parts.append("## Actions Taken")
                for tool_name, result in context.tool_results.items():
                    if tool_name == "list_data_documents":
                        continue  # Skip internal detail
                    if tool_name == "insert_records":
                        if hasattr(result, 'success') and result.success:
                            count = getattr(result, 'count', 0)
                            parts.append(f"- Inserted **{count}** records successfully")
                        elif hasattr(result, 'error'):
                            parts.append(f"- Insert failed: {result.error}")

                # Show what was extracted
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
            # Check for the ask-confirmation case
            matches = self._extracted_data.get("_matches", [])
            if matches and self._intent == INTENT_CREATE:
                parts = ["I found existing projects that match your request:\n"]
                for m in matches:
                    ext = m["extracted"]
                    ex = m["existing"]
                    parts.append(
                        f"- **{ext.get('name', '?')}** matches existing "
                        f"**{ex.get('name', '?')}** "
                        f"({ex.get('status', '?')}, {ex.get('progress', 0)}%)"
                    )
                parts.append(
                    "\nWould you like to update the existing projects "
                    "or create new entries?"
                )
                return "\n".join(parts)

            projects = self._extracted_data.get("projects", [])
            tasks = self._extracted_data.get("tasks", [])
            parts = ["Here's what I did:\n"]
            if projects:
                parts.append(f"**Created {len(projects)} projects:**")
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
        Override to store the original query for later use in intent classification.
        """
        # Store the original query in metadata so pipeline_steps/process_tool_result
        # can access it (the base class doesn't pass query to process_tool_result)
        if context is None:
            context = {}
        if "metadata" not in context or context["metadata"] is None:
            context["metadata"] = {}
        context["metadata"]["_original_query"] = query

        return await super().run_with_streaming(query, stream, cancel, context)


# Singleton instances
status_assistant_agent = StatusAssistantAgent()


class StatusUpdateAgent(BaseStreamingAgent):
    """
    A simpler status update agent focused on recording quick updates.

    Uses the same pipeline pattern but with a focus on:
    1. Querying current project/task state
    2. Applying updates based on user input
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

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        self._doc_ids = {}
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
            if hasattr(result, 'documents') and result.documents:
                for doc in result.documents:
                    name = doc.get("name", "")
                    doc_id = doc.get("id", "")
                    if "project" in name.lower():
                        self._doc_ids["projects"] = doc_id
                    elif "task" in name.lower():
                        self._doc_ids["tasks"] = doc_id

            # Always query projects and tasks for context
            steps = []
            if self._doc_ids.get("projects"):
                steps.append(PipelineStep(
                    tool="query_data",
                    args={
                        "document_id": self._doc_ids["projects"],
                        "select": ["id", "name", "status", "progress"],
                        "limit": 10,
                    },
                ))
            if self._doc_ids.get("tasks"):
                steps.append(PipelineStep(
                    tool="query_data",
                    args={
                        "document_id": self._doc_ids["tasks"],
                        "select": ["id", "projectId", "title", "status", "priority"],
                        "limit": 20,
                    },
                ))
            return steps

        return []

    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        parts = [f"## User's Update\n{query}\n"]

        if context.recent_messages:
            parts.append("## Recent Conversation")
            for msg in context.recent_messages[-4:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                parts.append(f"{role.title()}: {content}")
            parts.append("")

        parts.append("## Current Data")
        for tool_name, result in context.tool_results.items():
            if tool_name == "query_data" and hasattr(result, 'records'):
                for rec in result.records[:10]:
                    fields = [f"{k}: {v}" for k, v in rec.items() if k != "id" and v]
                    parts.append(f"- {', '.join(fields)}")

        parts.append("\nSummarize the current status and help the user update it.")
        return "\n".join(parts)


status_update_agent = StatusUpdateAgent()
