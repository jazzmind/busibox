# Roadmap Initiatives to Improve Busibox

## Core System

### Memory footprint
- takes up way too much memory
- might rewrite authz in rust/go
- any other rust/go candidates?
- might move portal and agent-manager to another stack (flutter?)

### Installation
- Minimum Requirements. Currently apple silicon, M4, 24gb OR 3090 gpu, 24 GB.
- create a better installer/manager script that explains choices
- basic mode - use case optimizes model selection, memory use, whether it's a prod deploy or app dev deploy (hot reload on user-apps), or core system dev deploy (hot reload on all)
- advanced mode - allow model selection, maybe vectordb, other components
- rewrite in rust
- make sure components don't time out during deploy install

---

## AI Models

- improve frontier model fallback 
    - chat when longer context needed
    - vision if our local models aren't multimodal
    - respect doc classification


# Agents & Tools

## Dispatcher
- Improve it's routing and tool usage; have profiles tuned to different model capabilities

## Feedback
- Feedback improves assistant dynamically via insights 
- Insights can include tool use suggestions

## Chat
- Create interactive buttons for simple chat items - e.g. select a folder, yes-no, get the chat to use those. Determine which bridge services can display those and have a text fallback.
[ ] - chat agent should be able to create agent tasks automatically
  [ ] dispatcher can recommend activation of tools, ask questions with yes/no buttons, option lists to click on. e.g. should I create an agent task for this? Yes / No` if yes - activates agent task tool. 
  [ ] - test is "send me a videogame news summary via email every hour"
  [ ] - should use "news agent"

## Scraper tool
    - convert all html to md using this approach https://blog.cloudflare.com/markdown-for-agents/ before processing


# Data Service
- make sure we don't try to convert md to md
- Tag schema fields for graph/vector embedding
- Don't do entity extraction unless there's a schema associated with the doc type
- Our autoschema gen should be smart enough to pre-tag
- upload all files to personal but then ask if the file should get moved as part of chat flow.
- folders contain sensitivity classification - e.g. local llm only
- tabular data ingestion
- use outline?


## Security
- claude code security scan everything
- Security validation section in "testing" that proves data security model interactively

## App Library
- Hook in security scanners - e.g. vibefunder analyzer

## Bridge
- telegram/sms/whatsapp formatting
- reply to email

## Voice Agent


--- Core Apps ---

## Agent Manager

## App Builder
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
