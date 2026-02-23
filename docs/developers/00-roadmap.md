# Roadmap Initiatives to Improve Busibox

## Core System

### Installation (2)
- Minimum Requirements. Currently apple silicon, M4, 24gb OR 3090 gpu, 24 GB.
- Dependencies - needs to help install correct docker, python and other necessary deps.
- Perhaps should boot up an install container with everything necessary? deploy-api container first?
- create a better installer/manager script that explains choices - maybe in rust?
- basic mode - use case optimizes model selection, memory use, whether it's a prod deploy or app dev deploy (hot reload on user-apps), or core system dev deploy (hot reload on all)
- advanced mode - allow model selection, maybe vectordb, other components
- make sure components don't time out during deploy install

---

## AI Models

### improve frontier model fallback (5)
    - chat when longer context needed
    - vision if our local models aren't multimodal
    - respect doc classification


## Data Processing (1)
- make sure we don't try to convert md to md
- Tag schema fields for graph/vector embedding
- Don't do entity extraction unless there's a schema associated with the doc type
- Our autoschema gen should be smart enough to pre-tag
- upload all files to personal but then ask if the file should get moved as part of chat flow.
- folders contain sensitivity classification - e.g. local llm only
- tabular data ingestion
- use outline?
- improving ingestion:
  [ ] tags are not working for documents properly. 
  [T] Fix doc ingestion
  [T] Add search: {index, embed, graph} to schema fields
  [ ] Trigger on move into folder
  [ ] Folder classification - add tags to folder, then when a doc matches it can be automatically moved in or prompt.
  [ ] long docs - split them
  [ ] lots of visuals - how does colpali handle them?
  [ ] Deep dive on colpali and how it works using diagram pdfs
  - test with large doc - greensheet

## Agents & Tools

## Dispatcher (3)
- Improve it's routing and tool usage; have profiles tuned to different model capabilities

## Feedback (3)
- Feedback improves assistant dynamically via insights 
- Insights can include tool use suggestions

## Chat (3)
- Create interactive buttons for simple chat items - e.g. select a folder, yes-no, get the chat to use those. Determine which bridge services can display those and have a text fallback.
[ ] - chat agent should be able to create agent tasks automatically
  [ ] dispatcher can recommend activation of tools, ask questions with yes/no buttons, option lists to click on. e.g. should I create an agent task for this? Yes / No` if yes - activates agent task tool. 
  [ ] - test is "send me a videogame news summary via email every hour"
  [ ] - should use "news agent"


2) We need to tune the chat agent's thinking to first check if there are relevant docs via document search - retrieve highly relevant docs and evaluate. Web search when getting more info is needed or requested, scrape results to determine if the information is helpful.

3) for these more complex flows we should use a multi-response approach. E.g. if the question involves cross referencing our docs against the web the first response should be something like "I found some relevant documents... summarizing then will search the web for more info." 
Then "Here's a quick summary of what I found... <summary> - now looking online."
Then "Here's a summary of what I found online <summary> - now putitng it all together"
Then final summary. This way we are streaming responses constantly vs. waiting a long time for a response to come in.

1) Thinking needs to work the same way in both fullchat and simplechat:
- when dispatcher is thinking, the toggle is open and updating
- as soon as we start streaming responses, close the toggle, but don't remove it
- the thinking history should be preserved with the message so it shows up when we reload the conversation
currently in fullchat the thinking toggle doesnt't appear immediately, is closed when it does, disappears as soon as the response has finished.




## Scraper tool (4)
    - convert all html to md using this approach https://blog.cloudflare.com/markdown-for-agents/ before processing


## Security (5)
- claude code security scan everything
- Security validation section in "testing" that proves data security model interactively

## App Library (6)
- Hook in security scanners - e.g. vibefunder analyzer

## Bridge (2)
- telegram/sms/whatsapp formatting
- reply to email

## Voice Agent (6)


--- Core Apps ---

## Agent Manager

## App Builder (2)
  - can use claude code to build apps in user-apps, deploy & iterate with browser use & log access
  - apps can be published to github OR kept private

--- Add-on Apps / Agents ---

# Project Manager

# Data Analysis
    [ ] needs to work with local llm
    [ ] report view
    [ ] do castle p&l analysis project

# Recruiter

# Paralegal
  - Flag issues in contracts
  - Have "reference" contract
  - Draft contracts

# Compliance

# Marketer
  - Researches & analyzes successful posts on relevant topics/platforms 
  - Creates optimized social media posts (substack, linkedin)

# Researcher
  - Notebook LM style
