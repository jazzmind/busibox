# Wes's Tasks
a) get cashman laptop running busibox
b) make sure ai-portal chat works with tavily + aws bedrock



Agent tasks:
2 - make sure all busibox-app tests pass
3 - make sure all authz tests pass
4 - make sure all ai-portal tests pass
5 - make sure agent-api is working and all tests pass
6 - start rebuilding agent-client (rename to agent-manager) using busibox-app libs and components
7 - make sure agent-manager runs and can talk to agent-api


What I'd delagate now:
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

## Data Analysis Tasks
1) Ability to add charts to a report
2) Ability to add time series data - e.g. upload the same sheet for multiple months
3) Ability to add a report to a dashboard


## Cashman Projects

### P&L Statement Analysis Platform
- [ ] We need a workflow: upload each monthly P&L statement, extract the data (P&L data extraction agent + data cleaning/validation agent), store as timeseries data in a database
- [ ] Another agent needs to be able to analyze the timeseries data based on questions
- [ ] We also want a charting agent that can take a description of a chart & dataset and generate chartJS code
Add a tool to the agent that can analyze a P&L statement and return a summary of the financial performance
