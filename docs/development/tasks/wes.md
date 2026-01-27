# Wes's Tasks
[ ] get full make install flow working
   [ ] embedding model pre-download, speed up container 
[ ] get app deployer to work with sample
[ ] update project-analysis to match app-template & deploy
[ ] same for estimator
[ ] improve project-analysis
    [ ] needs to work with local llm
    [ ] report view

## Active Tasks

[ ] tags are not working for documents properly. 
[ ] need "hero" flow: 
  - upload doc as chat attachment
  - have it do a doc search to find related
  - do a web search to get deeper info
  - summarize
[ ] claude code via busibox? clawd.bot?

 
## Future features
[ ] - agents can manage their own insights/memories and consult when running
[ ] - integration with whatsapp/sms, signal etc. (bridge service)
[ ] - improve existing agents - chat (has websearch, filesearch, upload), web search (focus on deep research), RAG Search Agent
[ ] - model registry overrides in env/dockercompose/ansible, or dynamically set during make install, or menu
[ ] a "warmup" screen for ai-portal when models need to be cached/downloaded.
[ ] dispatcher can recommend activation of tools, ask questions with yes/no buttons, option lists to click on. e.g. should I create an agent task for this? Yes / No if yes - activates agent task tool. 
[ ] tasks can be initiated by a document ingest. So we need an event to be emitted that the task manager listens to (webhook?) that gives library folder, keywords and tags for docs. Tasks will trigger based on matching keywords, tags, folders, etc.
vestiges.
[ ] - chat agent should be able to create agent tasks automatically
[ ] - test is "send me a videogame news summary via email every hour"
[ ] - should use "news agent"


2) We need to tune the chat agent's thinking to first check if there are relevant docs via document search - retrieve highly relevant docs and evaluate. Web search when getting more info is needed or requested, scrape results to determine if the information is helpful.

3) for these more complex flows we should use a multi-response approach. E.g. if the question involves cross referencing our docs against the web the first response should be something like "I found some relevant documents... summarizing then will search the web for more info." 
Then "Here's a quick summary of what I found... <summary> - now looking online."
Then "Here's a summary of what I found online <summary> - now putitng it all together"
Then final summary. This way we are streaming responses constantly vs. waiting a long time for a response to come in.

## What I'd delagate now:
- improving ingestion:
  [ ] long docs - split them
  [ ] lots of visuals - how does colpali handle them?

- tool calling model for phi-4
  [ ] how does it work?
  
- improving apps
  [ ] project analysis - needs to work better

--- 
1) Get agent client/server working on test environment
2) Make sure chat works with docs/agent server on test environment
--- deploy to production ---
1) Use a tool calling model in addition to Phi-4 to support tool calls
2) Deep dive on colpali and how it works using diagram pdfs
Issues:
1) Thinking needs to work the same way in both fullchat and simplechat:
- when dispatcher is thinking, the toggle is open and updating
- as soon as we start streaming responses, close the toggle, but don't remove it
- the thinking history should be preserved with the message so it shows up when we reload the conversation
currently in fullchat the thinking toggle doesnt't appear immediately, is closed when it does, disappears as soon as the response has finished.



## Recent Completions
[X] Get insights working
[X] Get agent tasks working with tool calling
[X] Deploy to staging
[X] - get agent manager to be able to run the web search tool via the web research agent
[X] - use this agent via the ai-portal chat
[X] - fix the document manager
[X] - get the doc search tool to work
[X] - get the doc search agent to work
[X] - create an email messenger tool that can send emails
[X] - now the ai-portal chat agent should be able to use web search AND doc search together
[X] - deploy all this to busibox test
[X] - create an "agent tasks" capability
    [X] - first add to agent manager
    [X] - then we want to have agents, tasks, insights as things accessible from chat
[X] - Fixed document_search tool authentication and configuration UI (2026-01-14)
  - Problem 1: document_search tool wasn't using authenticated API calls, causing permission errors
  - Problem 2: agent-manager showed irrelevant web_search provider options for document_search
  - Problem 3: Tool testing endpoint couldn't handle tools that require RunContext
  - Solution:
    - Updated `document_search_tool` to use `RunContext[BusiboxDeps]` for authenticated calls
    - Enhanced `BusiboxClient.search()` to support all search features (mode, file_ids, rerank, etc.)
    - Fixed `ToolConfigPanel` to only show providers for web_search, not document_search
    - Updated tool test endpoint to detect context-requiring tools and create MockRunContext with token exchange
  - Files changed:
    - `/Users/wsonnenreich/Code/busibox/srv/agent/app/tools/document_search_tool.py` - Added RunContext support
    - `/Users/wsonnenreich/Code/busibox/srv/agent/app/clients/busibox.py` - Enhanced search() method
    - `/Users/wsonnenreich/Code/busibox/srv/agent/app/api/tools.py` - Added RunContext handling in test endpoint
    - `/Users/wsonnenreich/Code/agent-manager/components/tools/ToolConfigPanel.tsx` - Fixed provider filter
    - `/Users/wsonnenreich/Code/busibox/srv/ingest/src/worker.py` - Added status update

[X] - Added HuggingFace model caching for faster rebuilds (2026-01-14)
  - Problem: Every Docker rebuild downloaded all safetensor models (~2GB+), taking 5-10 minutes
  - Solution: Added persistent Docker volume `huggingface_cache` mounted to `/root/.cache/huggingface`
  - Benefits:
    - Models downloaded once and reused across container rebuilds
    - Cache persists even after `docker compose down` (only cleared with `docker volume rm`)
    - Shared across ingest-api, ingest-worker, and search-api
    - Significantly faster rebuild times after first download
  - Files changed:
    - `/Users/wsonnenreich/Code/busibox/docker-compose.local.yml`
  - Models cached:
    - FastEmbed: `BAAI/bge-large-en-v1.5` (1024-d, ~1.3GB)
    - sentence-transformers (search-api)
    - Any future ColPali models

[X] - Fixed ingestion worker poppler dependency (2026-01-14)
  - Problem: Worker hanging with "Unable to get page count. Is poppler installed and in PATH?"
  - Root cause: Missing `poppler-utils` in Docker container for PDF page image extraction (ColPali)
  - Solution: 
    - Added `poppler-utils` and `libmagic1` to Dockerfile
    - Improved error handling in `_extract_pdf_page_images` to catch specific pdf2image exceptions
    - Changed error log from `debug` to `info` level for better visibility
  - Files changed:
    - `/Users/wsonnenreich/Code/busibox/srv/ingest/Dockerfile`
    - `/Users/wsonnenreich/Code/busibox/srv/ingest/src/processors/text_extractor.py`
  - Status: Requires Docker rebuild: `docker compose -f docker-compose.local.yml build ingest-worker ingest-api`

[X] - Fixed JWT token expiration/caching issue (2026-01-14)
  - Problem: Document uploads failing with "Invalid or expired JWT token"
  - Root cause: Tokens were cached and could expire between retrieval and use
  - Solution: Added automatic token refresh and retry logic on 401/403 errors
  - Files changed:
    - `/Users/wsonnenreich/Code/busibox-app/src/lib/ingest/client.ts` - Added retry logic with token refresh
    - `/Users/wsonnenreich/Code/ai-portal/src/lib/authz-client.ts` - Added token invalidation function
    - `/Users/wsonnenreich/Code/ai-portal/src/lib/ingest/client.ts` - Added token tracking and auto-invalidation

[X] - Fixed Docker deployment menu structure (2026-01-14)
  - Added missing `ingest-worker` to all service lists
  - Reorganized menu hierarchy:
    - Top level: All Services, API Services, Data Services, Individual Services, Clean-up
    - Each service group has: Build, Status, Restart, Start, Stop, Logs
  - Fixed service selection menus to include ingest-worker
