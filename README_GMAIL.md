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
* Google Auth Platform client creation:
  <https://support.google.com/cloud/answer/15549257?hl=en>

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

## Step-by-step setup

These steps assume you already have the Gmail account you want to use and only
need to connect Mail Custodian to it.

### 1. Confirm what you need

Before starting, make sure all of the following are true:

1. You can sign in to the Gmail account in a normal web browser.
2. You can run `mail-custodian` on the machine that will later run your rules.
3. That machine can open a local browser once for the Google sign-in flow, or
   you can copy and open a printed URL manually in a browser on the same
   machine.

### 2. Create or choose a Google Cloud project

1. Open the Google Auth Platform clients page:
   <https://console.developers.google.com/auth/clients>
2. If Google asks you to choose a project, either select an existing one or
   create a new project for Mail Custodian.
3. If Google says the application must be registered before a client can be
   created, complete that registration step in the Google Auth Platform UI and
   then return to the clients page.

### 3. Create a Desktop app OAuth client

1. On the clients page, click **Create client**.
2. Choose the application type for a desktop or installed application.
3. Give the client a recognizable name such as `Mail Custodian`.
4. Create the client.
5. Copy the **client ID**.
6. Copy the **client secret** immediately and store it somewhere safe. Google
   warns that the secret may not be shown again later.

Mail Custodian uses Google's installed-application OAuth flow and a local
loopback callback, so the OAuth client should be a **Desktop app** client.

### 4. Add the Gmail account to your Mail Custodian config

Add an account entry like this:

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

Set the client secret in your shell before authorizing:

```bash
export GMAIL_CLIENT_SECRET='your-client-secret'
```

You can also put `client_secret:` directly in the YAML, but
`client_secret_env` avoids storing it in plain text in the config file.

### 5. Run the one-time authorization command

Run:

```bash
mail-custodian --authorize-gmail personal-gmail
```

What Mail Custodian does:

1. Reads the Gmail account entry from your config.
2. Starts a temporary local callback listener on `127.0.0.1`.
3. Opens a browser to Google's sign-in and consent screen.
4. Prints the authorization URL if opening the browser fails.

### 6. Sign in to Google and approve access

In the browser:

1. Sign in to the Gmail account if needed.
2. Review the requested mail access.
3. Approve the request.
4. Wait for the browser page to say Mail Custodian received the authorization.

Mail Custodian then exchanges the returned authorization code for OAuth tokens
and stores the Gmail refresh token locally.

### 7. Verify that the refresh token was stored

The refresh token is stored in:

* `$XDG_STATE_HOME/mail-custodian/gmail-oauth.json` when `XDG_STATE_HOME`
  is set
* `~/.local/state/mail-custodian/gmail-oauth.json` otherwise

You do not need to edit that file manually.

### 8. Run Mail Custodian normally

After the one-time authorization step succeeds, use Mail Custodian normally:

```bash
mail-custodian --dry-run
```

or:

```bash
mail-custodian
```

Normal runs use the stored refresh token to obtain fresh Gmail IMAP access
tokens automatically.

## If the target host has no GUI

The simplest approach is usually to do the one-time Gmail authorization on a
different machine that does have browser access, then copy the stored refresh
token to the headless host.

### Recommended workflow

1. On a machine with a GUI, install Mail Custodian or otherwise make the
   `mail-custodian` command available.
2. Copy the same Mail Custodian config to that machine, or create a temporary
   config that contains the same Gmail account entry.
3. Make sure the Gmail account entry uses the same:
   * account `name`
   * `username`
   * `gmail_oauth.client_id`
   * `gmail_oauth.client_secret` or `gmail_oauth.client_secret_env`
4. Run:

   ```bash
   mail-custodian --authorize-gmail personal-gmail
   ```

5. Complete the browser-based Google sign-in and consent flow on that GUI
   system.
6. After authorization succeeds, copy the resulting `gmail-oauth.json` file to
   the headless host:
   * from `$XDG_STATE_HOME/mail-custodian/gmail-oauth.json` if
     `XDG_STATE_HOME` is set
   * otherwise from `~/.local/state/mail-custodian/gmail-oauth.json`
7. Place that file on the headless host in the matching Mail Custodian state
   location.
8. Make sure the Mail Custodian config on the headless host uses the same Gmail
   account `name`, because the stored refresh token is keyed by account name.
9. Run `mail-custodian --dry-run` on the headless host to confirm it can obtain
   a fresh access token and connect successfully.

### Why this is usually easier

Mail Custodian's authorization flow starts a temporary local callback listener
on `127.0.0.1`. On a headless remote system, that means the browser step is
awkward unless you also set up SSH tunneling or another way to reach that local
callback port. Copying the stored refresh token from a GUI-capable system is
usually simpler and only needs to be done once per Gmail account and OAuth
client combination.

## Troubleshooting

### Browser flow does not start

If Mail Custodian cannot open your browser automatically, copy the URL it
prints and open it manually in a browser on the same machine.

### Gmail rejects username/password authentication

That is expected here. Mail Custodian does not use `password` or
`password_env` for Gmail accounts; it uses OAuth 2.0 and XOAUTH2.

### You changed or deleted the OAuth client

If you change the Google OAuth client, the stored refresh token may no longer
work. Update the config and run `mail-custodian --authorize-gmail <account>`
again.

### You want to reconnect the account from scratch

Delete the stored Gmail OAuth file listed below and run
`mail-custodian --authorize-gmail <account>` again.

## Command

```bash
mail-custodian --authorize-gmail personal-gmail
```

## Stored state

Gmail refresh tokens obtained with `--authorize-gmail` are stored in:

* `$XDG_STATE_HOME/mail-custodian/gmail-oauth.json` when `XDG_STATE_HOME`
  is set
* `~/.local/state/mail-custodian/gmail-oauth.json` otherwise
