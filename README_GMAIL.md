# Gmail configuration

Mail Custodian can connect to Gmail IMAP accounts with Google OAuth 2.0 and
XOAUTH2.

For ordinary Gmail and Google Workspace user mailboxes, Mail Custodian uses
Google's desktop-app OAuth flow with the IMAP scope
`https://mail.google.com/`.

For ordinary Gmail accounts, this is a user-consent OAuth flow, not a
service-account or server-to-server integration. Mail Custodian does not
implement Google Workspace domain-wide delegation.

## Google references

* Gmail in third-party mail clients:
  <https://support.google.com/mail/answer/7126229?hl=en>
* Gmail XOAUTH2 for IMAP:
  <https://developers.google.com/workspace/gmail/imap/xoauth2-protocol>
* Google OAuth 2.0 for installed/desktop applications:
  <https://developers.google.com/identity/protocols/oauth2/native-app>

Google's current Gmail guidance says that for personal Google Accounts, IMAP
is always enabled, so the main setup task is the OAuth connection rather than
toggling a separate IMAP setting.

## Configuration

For Gmail accounts, do not use `password` or `password_env`. Use
`provider: gmail` plus a `gmail_oauth` block instead:

```yaml
accounts:
  - name: personal-gmail
    provider: gmail
    username: person@gmail.com
    gmail_oauth:
      client_id: your-desktop-app-client-id
      client_secret_env: GMAIL_CLIENT_SECRET
    rules:
      - name: Flag receipts
        criteria:
          subject_contains:
            - receipt
        actions:
          add_flags:
            - \Flagged
```

## Setup flow

1. Create a Google Cloud project and a **Desktop app** OAuth client.
2. Put the client ID and client secret in `gmail_oauth`.
3. Run `mail-custodian --authorize-gmail <account-name>` once.
4. Sign in with Google and grant access in the browser.
5. Run Mail Custodian normally after that.

The authorization command exchanges the returned authorization code for OAuth
tokens and stores the Gmail refresh token under the Mail Custodian state
directory. Normal runs use that stored refresh token to obtain fresh IMAP
access tokens automatically.

## Command

```bash
mail-custodian --authorize-gmail personal-gmail
```

## Stored state

Gmail refresh tokens obtained with `--authorize-gmail` are stored in:

* `$XDG_STATE_HOME/mail-custodian/gmail-oauth.json` when `XDG_STATE_HOME`
  is set
* `~/.local/state/mail-custodian/gmail-oauth.json` otherwise
