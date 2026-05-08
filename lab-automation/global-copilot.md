# Global Copilot Instructions — ijosh-ch

## Long-Term Memory (MySQL)

A `mysql-memory` MCP tool is available in VS Code. It connects to `llm_memory` on the BMW Lab VM via SSH tunnel (`localhost:3307`).

**At the START of every session**, run this to load recent context:

```sql
SELECT s.id, s.repo, s.start_at, s.end_at, s.commit_title, ss.summary
FROM sessions s
LEFT JOIN session_summaries ss ON ss.session_id = s.id
ORDER BY s.start_at DESC
LIMIT 5;
```

**At the END of every session**, before committing, insert records:

```sql
-- 1. Open session
INSERT INTO sessions (github_username, github_email, repo, start_at, end_at, base_commit, commit_title)
VALUES ('Ian Joseph Chandra', 'ianjoseph2204@gmail.com', '<owner/repo>', '<start>', '<end>', '<base_sha>', '<title>');
SET @sid = LAST_INSERT_ID();

-- 2. Log prompts (one row per key exchange)
INSERT INTO prompts (session_id, tool, prompted_at, prompt_summary, response_summary)
VALUES (@sid, 'copilot', '<timestamp>', '<prompt summary>', '<response summary>');

-- 3. Session summary (reused as git commit "Summary")
INSERT INTO session_summaries (session_id, summary)
VALUES (@sid, '<one-paragraph summary>');
```

After pushing: `UPDATE sessions SET result_commit = '<new_hash>' WHERE id = <sid>;`

## Identity

- GitHub: ijosh-ch / Ian Joseph Chandra
- Email: ianjoseph2204@gmail.com
- Lab: BMW Lab, NTUST — Supervisor: Prof. Ray
- Daily-log issue: bmw-ece-ntust/progress-plan#366
- Timezone: Asia/Taipei (GMT+8)
