## 2024-07-31 - [CRITICAL] Prevented API Key Stored in Plaintext (Config File)

**Vulnerability:** The application was previously storing the Anthropic Claude API key (`claude_api_key`) directly in the configuration file (`user_config.json`) in plaintext.
**Learning:** Even if the configuration file is stored in a user directory, plaintext storage of sensitive API keys leaves them vulnerable to theft via local malware, unintended sharing, or back-up systems that do not protect file contents adequately.
**Prevention:** Always use secure system credential managers (e.g. `keyring` in Python) when persisting API keys, credentials, or sensitive tokens. The configuration file was updated to only store references or non-sensitive data, delegating `claude_api_key` to the system's native keychain using a custom service name (e.g. `"BrightspacePagesAutomator_Claude"`).
