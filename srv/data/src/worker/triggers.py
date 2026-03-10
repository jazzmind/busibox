"""
Library trigger logic extracted from worker.py.

Contains TriggerMixin with methods for checking and firing library triggers:
- Pass-based trigger checks
- Library classification (auto-move, suggestions)
- Library trigger execution (run_agent, apply_schema, notify)
- Token exchange for audience-bound API calls
- Template rendering and execution recording
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()


class TriggerMixin:
    """Mixin providing library trigger methods for IngestWorker."""

    def _check_pass_triggers(
        self,
        file_id: str,
        user_id: str,
        delegation_token: Optional[str],
        current_pass: int,
    ):
        """
        Check if any library triggers should fire at this pass number.

        Reads the run_at_pass JSONB array from library_triggers and fires
        triggers whose array includes the current_pass.
        """
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT library_id FROM data_files WHERE file_id = %s",
                        (file_id,),
                    )
                    row = cur.fetchone()
            finally:
                self.postgres_service._return_connection(conn)

            if not row or not row[0]:
                return

            library_id = str(row[0])

            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, run_at_pass
                        FROM library_triggers
                        WHERE library_id = %s AND is_active = true
                    """, (library_id,))
                    triggers = cur.fetchall()
            finally:
                self.postgres_service._return_connection(conn)

            if not triggers:
                return

            # Filter triggers whose run_at_pass includes current_pass
            triggers_to_fire = []
            for trigger_id, run_at_pass in triggers:
                passes = run_at_pass if isinstance(run_at_pass, list) else [3]
                if current_pass in passes:
                    triggers_to_fire.append(str(trigger_id))

            if not triggers_to_fire:
                return

            logger.info(
                "Firing triggers for pass",
                file_id=file_id,
                current_pass=current_pass,
                trigger_count=len(triggers_to_fire),
            )

            self._check_library_triggers(
                file_id, user_id, delegation_token,
                trigger_ids=triggers_to_fire,
            )

        except Exception as e:
            logger.warning(
                "Pass trigger check failed (non-fatal)",
                file_id=file_id,
                current_pass=current_pass,
                error=str(e),
            )

    def _check_library_classification(
        self,
        file_id: str,
        user_id: str,
    ):
        """
        Check if the completed document matches classification rules on any library.

        For personal libraries: auto-move the document if it matches.
        For shared libraries: store suggestions in document metadata.

        This is non-blocking -- failures do not affect document processing.
        """
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    # Get the document's classification info
                    cur.execute("""
                        SELECT file_id, library_id, extracted_keywords, document_type,
                               classification_confidence, user_id
                        FROM data_files WHERE file_id = %s
                    """, (file_id,))
                    doc = cur.fetchone()
            finally:
                self.postgres_service._return_connection(conn)

            if not doc:
                return

            doc_library_id = str(doc[1]) if doc[1] else None
            doc_keywords = list(doc[2]) if doc[2] else []
            doc_type = doc[3]
            doc_confidence = doc[4] or 0.0
            doc_user_id = str(doc[5]) if doc[5] else user_id

            if not doc_keywords and not doc_type:
                return

            doc_keywords_lower = {kw.lower() for kw in doc_keywords}

            # Fetch libraries with classification config (rules or keywords)
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, name, is_personal, user_id, metadata
                        FROM libraries
                        WHERE deleted_at IS NULL
                          AND (
                            (metadata->'classificationRules' IS NOT NULL
                             AND jsonb_array_length(COALESCE(metadata->'classificationRules', '[]'::jsonb)) > 0)
                            OR
                            (metadata->'keywords' IS NOT NULL
                             AND jsonb_array_length(COALESCE(metadata->'keywords', '[]'::jsonb)) > 0)
                          )
                          AND (is_personal = false OR (is_personal = true AND user_id = %s::uuid))
                    """, (doc_user_id,))
                    candidate_libraries = cur.fetchall()
            finally:
                self.postgres_service._return_connection(conn)

            if not candidate_libraries:
                return

            suggestions = []
            moved = False

            for lib_row in candidate_libraries:
                lib_id = str(lib_row[0])
                lib_name = lib_row[1]
                lib_is_personal = lib_row[2]
                lib_metadata = lib_row[4] if lib_row[4] else {}

                if isinstance(lib_metadata, str):
                    lib_metadata = json.loads(lib_metadata)

                # Skip the library the document is already in
                if lib_id == doc_library_id:
                    continue

                rules = lib_metadata.get("classificationRules", [])
                lib_keywords_list = [kw.lower() for kw in lib_metadata.get("keywords", [])]
                lib_keywords = set(lib_keywords_list)

                # Simple mode: keywords exist but no rules -- synthesize an implicit suggest rule
                if not rules and lib_keywords_list:
                    rules = [{
                        "keywords": lib_keywords_list,
                        "documentTypes": [],
                        "action": "auto_move" if lib_is_personal else "suggest",
                        "minConfidence": 0.3,
                    }]

                best_score = 0.0
                matched_keywords = set()
                best_action = "copy"

                for rule in rules:
                    rule_keywords = {kw.lower() for kw in rule.get("keywords", [])}
                    rule_doc_types = [dt.lower() for dt in rule.get("documentTypes", [])]
                    rule_min_confidence = rule.get("minConfidence", 0.3)
                    rule_action = rule.get("action", "copy")

                    # Check confidence threshold
                    if doc_confidence < rule_min_confidence:
                        continue

                    # Score: keyword overlap
                    keyword_matches = doc_keywords_lower & rule_keywords
                    lib_keyword_matches = doc_keywords_lower & lib_keywords
                    all_matches = keyword_matches | lib_keyword_matches

                    if not all_matches and rule_doc_types:
                        # No keyword match -- check doc type
                        if doc_type and doc_type.lower() in rule_doc_types:
                            all_matches = {doc_type.lower()}

                    if not all_matches:
                        continue

                    # Compute match score
                    total_rule_keywords = len(rule_keywords | lib_keywords) or 1
                    score = len(all_matches) / total_rule_keywords

                    if doc_type and rule_doc_types and doc_type.lower() in rule_doc_types:
                        score = min(score + 0.2, 1.0)

                    if score > best_score:
                        best_score = score
                        matched_keywords = all_matches
                        best_action = rule_action

                if best_score > 0 and matched_keywords:
                    if lib_is_personal and best_action == "auto_move" and not moved:
                        # Auto-move for personal libraries
                        try:
                            conn = self.postgres_service._get_connection(self._current_rls_context)
                            try:
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        UPDATE data_files
                                        SET library_id = %s::uuid, updated_at = NOW()
                                        WHERE file_id = %s
                                    """, (lib_id, file_id))
                                    conn.commit()
                            finally:
                                self.postgres_service._return_connection(conn)

                            moved = True
                            logger.info(
                                "Auto-moved document to personal library",
                                file_id=file_id,
                                from_library=doc_library_id,
                                to_library=lib_id,
                                to_library_name=lib_name,
                                match_score=best_score,
                                matched_keywords=list(matched_keywords),
                            )
                        except Exception as move_err:
                            logger.error(
                                "Failed to auto-move document",
                                file_id=file_id,
                                target_library=lib_id,
                                error=str(move_err),
                            )
                    else:
                        # Store as suggestion for shared libraries (or if not auto_move)
                        suggestions.append({
                            "libraryId": lib_id,
                            "libraryName": lib_name,
                            "matchScore": round(best_score, 2),
                            "matchedKeywords": sorted(matched_keywords),
                            "suggestedAction": best_action,
                        })

            # Store suggestions in document metadata if any
            if suggestions:
                # Sort by score descending
                suggestions.sort(key=lambda s: s["matchScore"], reverse=True)

                try:
                    conn = self.postgres_service._get_connection(self._current_rls_context)
                    try:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE data_files
                                SET metadata = COALESCE(metadata, '{}'::jsonb)
                                    || jsonb_build_object('classificationSuggestions', %s::jsonb),
                                    updated_at = NOW()
                                WHERE file_id = %s
                            """, (json.dumps(suggestions), file_id))
                            conn.commit()
                    finally:
                        self.postgres_service._return_connection(conn)

                    logger.info(
                        "Stored classification suggestions",
                        file_id=file_id,
                        suggestion_count=len(suggestions),
                        top_library=suggestions[0]["libraryName"] if suggestions else None,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to store classification suggestions",
                        file_id=file_id,
                        error=str(e),
                    )

        except Exception as e:
            logger.error(
                "Library classification check failed (non-fatal)",
                file_id=file_id,
                error=str(e),
                exc_info=True,
            )

    def _check_library_triggers(
        self,
        file_id: str,
        user_id: str,
        delegation_token: Optional[str] = None,
        trigger_ids: Optional[list] = None,
    ):
        """
        Check if the completed file's library has any active triggers.
        If so, fire them by calling the Agent API.

        When *trigger_ids* is provided only those triggers are fired (used by
        ``_check_pass_triggers`` to respect ``run_at_pass`` filtering).

        This is non-blocking -- failures here do not affect the document processing result.
        """
        try:
            # Get the file's library_id
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT library_id, filename, markdown_path, has_markdown,
                               extracted_title, mime_type
                        FROM data_files WHERE file_id = %s
                    """, (file_id,))
                    row = cur.fetchone()
            finally:
                self.postgres_service._return_connection(conn)

            if not row or not row[0]:
                # No library_id -- clear any queued trigger status to avoid stale UI.
                self._set_file_trigger_status(
                    file_id=file_id,
                    status={
                        "state": "completed",
                        "completedAt": datetime.utcnow().isoformat(),
                        "triggerCount": 0,
                        "completedCount": 0,
                        "failedCount": 0,
                    },
                )
                return

            library_id = str(row[0])
            filename = row[1]
            markdown_path = row[2]
            has_markdown = row[3]
            extracted_title = row[4]
            mime_type = row[5]

            # Check for active triggers on this library
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, trigger_type, agent_id, prompt, schema_document_id,
                               notification_config, delegation_token, name,
                               record_defaults
                        FROM library_triggers
                        WHERE library_id = %s AND is_active = true
                    """, (library_id,))
                    triggers = cur.fetchall()
            finally:
                self.postgres_service._return_connection(conn)

            if trigger_ids is not None:
                id_set = set(trigger_ids)
                triggers = [t for t in triggers if str(t[0]) in id_set]

            if not triggers:
                # Upload may have pre-marked the file as trigger pending, but active
                # triggers may have been removed/disabled before this check runs.
                # Clear queued state so the UI can exit trigger-poll mode.
                self._set_file_trigger_status(
                    file_id=file_id,
                    status={
                        "state": "completed",
                        "completedAt": datetime.utcnow().isoformat(),
                        "triggerCount": 0,
                        "completedCount": 0,
                        "failedCount": 0,
                    },
                )
                return

            logger.info(
                "Library triggers found for completed document",
                file_id=file_id,
                library_id=library_id,
                trigger_count=len(triggers),
            )

            # Mark per-file trigger status so UI can show "running" and poll until complete
            self._set_file_trigger_status(
                file_id=file_id,
                status={
                    "state": "running",
                    "startedAt": datetime.utcnow().isoformat(),
                    "triggerCount": len(triggers),
                    "completedCount": 0,
                    "failedCount": 0,
                },
            )

            # Get markdown content if available
            markdown_content = None
            if has_markdown and markdown_path:
                try:
                    response = self.file_service.client.get_object(
                        bucket_name=self.file_service.bucket,
                        object_name=markdown_path,
                    )
                    markdown_content = response.read().decode('utf-8')
                    response.close()
                    response.release_conn()
                except Exception as e:
                    logger.warning(
                        "Failed to read markdown for trigger",
                        file_id=file_id,
                        error=str(e),
                    )

            # Get schema content for each trigger that references a schema document
            completed_count = 0
            failed_count = 0
            for trigger in triggers:
                trigger_id = str(trigger[0])
                trigger_type = str(trigger[1] or "run_agent")
                agent_id = str(trigger[2]) if trigger[2] else None
                trigger_prompt = trigger[3]
                schema_document_id = str(trigger[4]) if trigger[4] else None
                notification_config = trigger[5]
                trigger_delegation_token = trigger[6]
                trigger_name = trigger[7]
                raw_record_defaults = trigger[8] if len(trigger) > 8 else None

                trigger_record_defaults: Optional[dict] = None
                if raw_record_defaults:
                    if isinstance(raw_record_defaults, dict):
                        trigger_record_defaults = raw_record_defaults
                    elif isinstance(raw_record_defaults, str):
                        try:
                            trigger_record_defaults = json.loads(raw_record_defaults)
                        except Exception:
                            pass

                if isinstance(notification_config, str):
                    try:
                        notification_config = json.loads(notification_config)
                    except Exception:
                        notification_config = None

                if trigger_type == "notify":
                    success = self._fire_notification_trigger(
                        trigger_id=trigger_id,
                        trigger_name=trigger_name,
                        file_id=file_id,
                        filename=filename,
                        extracted_title=extracted_title,
                        library_id=library_id,
                        user_id=user_id,
                        mime_type=mime_type,
                        notification_config=notification_config or {},
                    )
                    completed_count += 1
                    if not success:
                        failed_count += 1
                    continue

                if trigger_type == "apply_schema" and not schema_document_id:
                    logger.warning(
                        "Apply-schema trigger has no schema_document_id, skipping",
                        trigger_id=trigger_id,
                    )
                    failed_count += 1
                    completed_count += 1
                    continue

                if trigger_type == "run_agent" and not agent_id:
                    logger.warning(
                        "Run-agent trigger has no agent_id, skipping",
                        trigger_id=trigger_id,
                    )
                    failed_count += 1
                    completed_count += 1
                    continue

                # Get schema if referenced
                schema_content = None
                if schema_document_id:
                    try:
                        conn = self.postgres_service._get_connection(self._current_rls_context)
                        try:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    SELECT data_schema FROM data_files
                                    WHERE file_id = %s AND doc_type = 'data'
                                """, (schema_document_id,))
                                schema_row = cur.fetchone()
                                if schema_row and schema_row[0]:
                                    schema_content = schema_row[0] if isinstance(schema_row[0], dict) else json.loads(schema_row[0])
                        finally:
                            self.postgres_service._return_connection(conn)
                    except Exception as e:
                        logger.warning(
                            "Failed to load schema for trigger",
                            trigger_id=trigger_id,
                            schema_document_id=schema_document_id,
                            error=str(e),
                        )

                # Fire the trigger via Agent API
                success = self._fire_library_trigger(
                    trigger_id=trigger_id,
                    trigger_name=trigger_name,
                    trigger_type=trigger_type,
                    agent_id=agent_id,
                    prompt=trigger_prompt,
                    file_id=file_id,
                    filename=filename,
                    extracted_title=extracted_title,
                    markdown_content=markdown_content,
                    schema_content=schema_content,
                    schema_document_id=schema_document_id,
                    user_id=user_id,
                    library_id=library_id,
                    # Prefer per-job delegation token first (fresh token from upload/reprocess).
                    # Trigger-level token is a fallback and may be stale.
                    delegation_token=delegation_token or trigger_delegation_token,
                    record_defaults=trigger_record_defaults,
                )
                completed_count += 1
                if not success:
                    failed_count += 1

            self._set_file_trigger_status(
                file_id=file_id,
                status={
                    "state": "failed" if failed_count > 0 else "completed",
                    "completedAt": datetime.utcnow().isoformat(),
                    "triggerCount": len(triggers),
                    "completedCount": completed_count,
                    "failedCount": failed_count,
                },
            )

        except Exception as e:
            logger.error(
                "Library trigger check failed (non-fatal)",
                file_id=file_id,
                error=str(e),
                exc_info=True,
            )
            self._set_file_trigger_status(
                file_id=file_id,
                status={
                    "state": "failed",
                    "completedAt": datetime.utcnow().isoformat(),
                    "error": str(e),
                },
            )

    def _fire_library_trigger(
        self,
        trigger_id: str,
        trigger_name: str,
        trigger_type: str,
        agent_id: Optional[str],
        prompt: Optional[str],
        file_id: str,
        filename: str,
        extracted_title: Optional[str],
        markdown_content: Optional[str],
        schema_content: Optional[dict],
        schema_document_id: Optional[str],
        user_id: str,
        library_id: str,
        delegation_token: Optional[str],
        record_defaults: Optional[dict] = None,
    ) -> bool:
        """Fire a single library trigger by calling the Agent API."""
        # Extraction can take longer than regular webhook/workflow calls.
        # Make trigger timeouts configurable with safe defaults.
        default_timeout = int(os.environ.get("LIBRARY_TRIGGER_HTTP_TIMEOUT", "90"))
        extract_timeout = int(
            os.environ.get("LIBRARY_TRIGGER_EXTRACT_TIMEOUT", str(max(default_timeout, 300)))
        )

        agent_api_url = self.config.get("agent_api_url") or os.environ.get("AGENT_API_URL", "")
        if not agent_api_url:
            # Try to construct from environment
            agent_host = os.environ.get("AGENT_API_HOST", "")
            agent_port = os.environ.get("AGENT_API_PORT", "8000")
            if agent_host:
                agent_api_url = f"http://{agent_host}:{agent_port}"
            else:
                error_msg = "No agent_api_url configured (AGENT_API_URL/AGENT_API_HOST missing)"
                logger.warning(
                    "Cannot fire library trigger",
                    trigger_id=trigger_id,
                    error=error_msg,
                )
                self._record_trigger_execution(trigger_id=trigger_id, error=error_msg)
                return False

        try:
            # Agent API validates audience=agent-api, while delegation tokens created
            # at upload time are generic. Exchange once and reuse for this trigger call.
            agent_api_token = delegation_token
            if delegation_token:
                exchanged = self._exchange_token_for_audience(
                    subject_token=delegation_token,
                    target_audience="agent-api",
                    user_id=user_id,
                )
                if exchanged:
                    agent_api_token = exchanged

            default_prompt = (
                f"Process the completed document '{filename}'. "
                f"file_id={file_id}, library_id={library_id}."
            )
            effective_prompt = prompt or default_prompt

            # For normal run triggers, include source markdown context.
            if trigger_type == "run_agent" and markdown_content:
                effective_prompt = (
                    f"{effective_prompt}\n\n"
                    f"Document title: {extracted_title or filename}\n"
                    f"Document content:\n{markdown_content[:20000]}"
                )

            if trigger_type == "apply_schema" or (trigger_type == "run_agent" and schema_document_id):
                # For apply_schema, match manual document-view behavior:
                # only pass prompt_override when explicitly configured on the trigger.
                # Otherwise let /extract use its built-in extraction instructions.
                schema_prompt_override = (
                    prompt if trigger_type == "apply_schema" else effective_prompt
                )
                extract_payload = {
                    "file_id": file_id,
                    "schema_document_id": schema_document_id,
                    "prompt_override": schema_prompt_override,
                    "store_results": True,
                    "user_id": user_id,
                    "delegation_token": delegation_token,
                }
                if agent_id:
                    extract_payload["agent_id"] = agent_id
                if record_defaults:
                    extract_payload["record_defaults"] = record_defaults

                req = urllib.request.Request(
                    f"{agent_api_url}/extract",
                    data=json.dumps(extract_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                if agent_api_token:
                    req.add_header("Authorization", f"Bearer {agent_api_token}")

                response = urllib.request.urlopen(req, timeout=30)
                response_data = json.loads(response.read().decode("utf-8"))

                # Extraction now runs asynchronously; poll until done.
                task_id = response_data.get("taskId")
                if task_id and response_data.get("status") == "accepted":
                    logger.info(
                        "Extraction accepted, polling for completion",
                        trigger_id=trigger_id,
                        task_id=task_id,
                        file_id=file_id,
                    )
                    poll_interval = 5
                    elapsed = 0
                    while elapsed < extract_timeout:
                        time.sleep(poll_interval)
                        elapsed += poll_interval
                        try:
                            status_req = urllib.request.Request(
                                f"{agent_api_url}/extract/status/{task_id}",
                                method="GET",
                            )
                            if agent_api_token:
                                status_req.add_header("Authorization", f"Bearer {agent_api_token}")
                            status_resp = urllib.request.urlopen(status_req, timeout=15)
                            status_data = json.loads(status_resp.read().decode("utf-8"))
                            task_status = status_data.get("status", "")
                            if task_status == "completed":
                                response_data = status_data.get("result", response_data)
                                logger.info(
                                    "Background extraction completed",
                                    trigger_id=trigger_id,
                                    task_id=task_id,
                                    elapsed=elapsed,
                                )
                                break
                            elif task_status == "failed":
                                error_msg = status_data.get("error", "Extraction failed")
                                raise Exception(f"Background extraction failed: {error_msg}")
                        except urllib.error.HTTPError:
                            pass
                        if poll_interval < 15:
                            poll_interval = min(poll_interval + 2, 15)
                    else:
                        logger.warning(
                            "Extraction polling timed out, but task continues in background",
                            trigger_id=trigger_id,
                            task_id=task_id,
                            timeout=extract_timeout,
                        )
            else:
                # Attempt workflow execution first; if not a workflow ID, fallback to agent webhook.
                response_data = None
                if agent_id:
                    workflow_payload = {
                        "input_data": {
                            "prompt": effective_prompt,
                            "file_id": file_id,
                            "filename": filename,
                            "title": extracted_title,
                            "library_id": library_id,
                            "trigger_id": trigger_id,
                        }
                    }
                    workflow_req = urllib.request.Request(
                        f"{agent_api_url}/agents/workflows/{agent_id}/execute",
                        data=json.dumps(workflow_payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    if agent_api_token:
                        workflow_req.add_header("Authorization", f"Bearer {agent_api_token}")
                    try:
                        workflow_response = urllib.request.urlopen(workflow_req, timeout=default_timeout)
                        response_data = json.loads(workflow_response.read().decode("utf-8"))
                    except urllib.error.HTTPError as workflow_err:
                        if workflow_err.code not in (400, 401, 403, 404, 422):
                            raise

                if response_data is None:
                    webhook_payload = {
                        "trigger_id": trigger_id,
                        "agent_id": agent_id,
                        "prompt": effective_prompt,
                        "file_id": file_id,
                        "user_id": user_id,
                        "library_id": library_id,
                        "schema_document_id": schema_document_id,
                        "delegation_token": delegation_token,
                    }
                    webhook_req = urllib.request.Request(
                        f"{agent_api_url}/webhooks/library-trigger",
                        data=json.dumps(webhook_payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    if agent_api_token:
                        webhook_req.add_header("Authorization", f"Bearer {agent_api_token}")
                    webhook_response = urllib.request.urlopen(webhook_req, timeout=default_timeout)
                    response_data = json.loads(webhook_response.read().decode("utf-8"))

            logger.info(
                "Library trigger fired successfully",
                trigger_id=trigger_id,
                trigger_name=trigger_name,
                trigger_type=trigger_type,
                file_id=file_id,
                agent_id=agent_id,
                response=response_data,
            )
            self._record_trigger_execution(trigger_id=trigger_id, error=None)
            return True

        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                error_body = ""
            error_msg = str(e)
            if error_body:
                error_msg = f"{error_msg}: {error_body[:500]}"
            logger.error(
                "Failed to fire library trigger",
                trigger_id=trigger_id,
                trigger_name=trigger_name,
                trigger_type=trigger_type,
                file_id=file_id,
                status_code=getattr(e, "code", None),
                error=error_msg,
            )
            self._record_trigger_execution(trigger_id=trigger_id, error=error_msg)
            return False

        except urllib.error.URLError as e:
            error_msg = str(e)
            logger.error(
                "Failed to fire library trigger",
                trigger_id=trigger_id,
                trigger_name=trigger_name,
                trigger_type=trigger_type,
                file_id=file_id,
                error=error_msg,
            )
            self._record_trigger_execution(trigger_id=trigger_id, error=error_msg)
            return False

        except Exception as e:
            logger.error(
                "Library trigger fire failed unexpectedly",
                trigger_id=trigger_id,
                file_id=file_id,
                error=str(e),
                exc_info=True,
            )
            self._record_trigger_execution(trigger_id=trigger_id, error=str(e))
            return False

    def _exchange_token_for_audience(
        self,
        subject_token: str,
        target_audience: str,
        user_id: str,
    ) -> Optional[str]:
        """Exchange a subject token for an audience-bound token via authz."""
        if not subject_token:
            return None

        authz_token_url = (
            self.config.get("authz_token_url")
            or os.environ.get("AUTHZ_TOKEN_URL")
            or os.environ.get("AUTH_TOKEN_URL")
        )
        if not authz_token_url:
            authz_base_url = self.config.get("authz_base_url") or os.environ.get("AUTHZ_BASE_URL")
            if authz_base_url:
                authz_token_url = f"{str(authz_base_url).rstrip('/')}/oauth/token"
            else:
                authz_token_url = "http://authz-api:8010/oauth/token"

        payload = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "audience": target_audience,
                "scope": "",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            authz_token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(req, timeout=10)
            data = json.loads(response.read().decode("utf-8"))
            access_token = data.get("access_token")
            if access_token:
                return str(access_token)
            logger.warning(
                "Token exchange returned no access_token",
                user_id=user_id,
                target_audience=target_audience,
            )
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            logger.warning(
                "Token exchange failed for trigger call",
                user_id=user_id,
                target_audience=target_audience,
                error=str(e),
            )
        except Exception as e:
            logger.warning(
                "Unexpected token exchange error",
                user_id=user_id,
                target_audience=target_audience,
                error=str(e),
            )
        return None

    def _render_trigger_template(self, template: Optional[str], values: dict, default: str) -> str:
        if not template:
            return default
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", "" if value is None else str(value))
        return rendered

    def _record_trigger_execution(self, trigger_id: str, error: Optional[str] = None):
        """Update trigger execution metadata in Postgres (non-fatal on failure)."""
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    if error:
                        cur.execute("""
                            UPDATE library_triggers
                            SET execution_count = execution_count + 1,
                                last_execution_at = NOW(),
                                last_error = %s,
                                updated_at = NOW()
                            WHERE id = %s
                        """, (error, trigger_id))
                    else:
                        cur.execute("""
                            UPDATE library_triggers
                            SET execution_count = execution_count + 1,
                                last_execution_at = NOW(),
                                last_error = NULL,
                                updated_at = NOW()
                            WHERE id = %s
                        """, (trigger_id,))
                    conn.commit()
            finally:
                self.postgres_service._return_connection(conn)
        except Exception:
            pass

    def _set_file_trigger_status(self, file_id: str, status: Dict[str, Any]):
        """Persist per-file trigger status inside data_files.metadata."""
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE data_files
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('triggerStatus', %s::jsonb),
                            updated_at = NOW()
                        WHERE file_id = %s
                        """,
                        (json.dumps(status), file_id),
                    )
                    conn.commit()
            finally:
                self.postgres_service._return_connection(conn)
        except Exception:
            # Non-fatal; trigger execution should continue
            pass

    def _fire_notification_trigger(
        self,
        trigger_id: str,
        trigger_name: str,
        file_id: str,
        filename: str,
        extracted_title: Optional[str],
        library_id: str,
        user_id: str,
        mime_type: Optional[str],
        notification_config: dict,
    ) -> bool:
        """Send a post-processing notification via bridge email or webhook."""
        channel = str(notification_config.get("channel", "email")).lower()
        recipient = notification_config.get("recipient")
        if not recipient:
            self._record_trigger_execution(trigger_id=trigger_id, error="Missing notification recipient")
            return False

        template_values = {
            "fileId": file_id,
            "file_id": file_id,
            "filename": filename,
            "title": extracted_title or filename,
            "libraryId": library_id,
            "library_id": library_id,
            "userId": user_id,
            "user_id": user_id,
            "status": "completed",
            "mimeType": mime_type or "",
            "mime_type": mime_type or "",
        }

        subject = self._render_trigger_template(
            notification_config.get("subjectTemplate"),
            template_values,
            f"Document processed: {filename}",
        )
        body = self._render_trigger_template(
            notification_config.get("bodyTemplate"),
            template_values,
            (
                f"Document processing completed.\n"
                f"Filename: {filename}\n"
                f"Title: {extracted_title or filename}\n"
                f"File ID: {file_id}\n"
                f"Library ID: {library_id}\n"
                f"User ID: {user_id}\n"
            ),
        )

        try:
            if channel == "webhook":
                payload = json.dumps(
                    {
                        "event": "ingestion.completed",
                        "trigger_id": trigger_id,
                        "trigger_name": trigger_name,
                        "subject": subject,
                        "message": body,
                        "file": {
                            "id": file_id,
                            "filename": filename,
                            "title": extracted_title or filename,
                            "mime_type": mime_type,
                            "library_id": library_id,
                            "user_id": user_id,
                            "status": "completed",
                        },
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    str(recipient),
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                response = urllib.request.urlopen(req, timeout=15)
                response.read()
            else:
                bridge_api_url = (
                    notification_config.get("bridgeApiUrl")
                    or self.config.get("bridge_api_url")
                    or os.environ.get("BRIDGE_API_URL")
                )
                if not bridge_api_url:
                    raise ValueError("BRIDGE_API_URL is not configured for email notifications")
                payload = json.dumps(
                    {
                        "to": recipient,
                        "subject": subject,
                        "html": body,
                        "text": body,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"{str(bridge_api_url).rstrip('/')}/api/v1/email/send",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                response = urllib.request.urlopen(req, timeout=15)
                response.read()

            logger.info(
                "Library notification trigger fired successfully",
                trigger_id=trigger_id,
                trigger_name=trigger_name,
                channel=channel,
                recipient=recipient,
                file_id=file_id,
            )
            self._record_trigger_execution(trigger_id=trigger_id, error=None)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            logger.error(
                "Library notification trigger failed",
                trigger_id=trigger_id,
                trigger_name=trigger_name,
                channel=channel,
                recipient=recipient,
                error=str(e),
            )
            self._record_trigger_execution(trigger_id=trigger_id, error=str(e))
            return False
