# Debug and Changelogs

## 1st Dec 2025

The reason which the chat page disappears after refresh, is due to currently all running session variable are stored as global state. This is no good for chat function, as the necessary struct `ESGChatbot` are lost when reloading. 

Therefore we propose to use chat id, particularly the generated report ID (since it is unique already) as the chat session ID (`/api/chat/{file_id}`). We also need

- Disk persistence by saving chat history to a loadable JSON
- Load specific report and context (currently works by global state reloading on each chat session exit)


## ADD

- UI folddown for 4 trunks on the analysis page.
- Cross-comparison between reports of similar main-industry (i.e. have chatbot compare multiple reports from Technology & Communications with past reports from Dell, IBM, Microsoft etc..)


## ChatID


## UI adaptation



