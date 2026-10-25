# Google setup

The app reads and changes your Google Tasks, Calendar and Gmail. (The Fitbit data through Google Health is in the
health_dashboard app: see its docs/google-health-setup.md.)
For this it needs one Google Cloud project with an OAuth client. You set it up once. Use the Google account whose data
the app must read.

All console links open the project that is selected at the top of the console. Check that it is the right project.

## 1. Make a project

1. Open [Create a project](https://console.cloud.google.com/projectcreate).
2. Give it a name, then click **Create**.
3. Select the new project at the top of the console.

If you already have a project for this app, select it and go to step 2.

## 2. Enable the APIs

Open each link and click **Enable**:

| For | API | Link |
|---|---|---|
| Tasks | Google Tasks API | [Enable](https://console.cloud.google.com/apis/enableflow;apiid=tasks.googleapis.com) |
| Calendar | Google Calendar API | [Enable](https://console.cloud.google.com/apis/enableflow;apiid=calendar-json.googleapis.com) |
| Gmail | Gmail API | [Enable](https://console.cloud.google.com/apis/enableflow;apiid=gmail.googleapis.com) |

## 3. Set up the Google Auth platform

1. [Branding](https://console.developers.google.com/auth/branding): set an app name, your email as the user support
   email and as the developer contact. Click **Save**.
2. [Audience](https://console.developers.google.com/auth/audience):
   - User type: **External**.
   - Publishing status: click **Publish app**, so that it shows **In production**.
     In **Testing**, the login stops working after 7 days.
3. [Data access](https://console.developers.google.com/auth/scopes): click **Add or remove scopes**. Add these scopes,
   paste the ones that are not in the list under **Manually add scopes**, then click **Update** and **Save**:

   | For | Scope |
   |---|---|
   | Tasks | `https://www.googleapis.com/auth/tasks` |
   | Calendar | `https://www.googleapis.com/auth/calendar` |
   | Gmail | `https://www.googleapis.com/auth/gmail.modify` |

The app is not verified by Google. That is no problem for your own use: an unverified app can have up to 100 users.

## 4. Make the OAuth client

1. Open [Clients](https://console.developers.google.com/auth/clients) and click **Create client**.
2. Application type: **Desktop app**. Give it a name, then click **Create**.
3. Click **Download JSON**.
4. Save the file in this project as `secrets/client_secret.json`.

The `secrets/` folder is in `.gitignore`. Never put the client file or a token anywhere else, and never commit them.

## 5. Log in

Tasks, Calendar and Gmail: click **Log in to Google** in the app, or run:

```bash
uv run python -m automation_desk.google_auth
```

1. Your browser opens the Google login. Choose your account.
2. Google shows "Google hasn't verified this app". Click **Advanced**, then **Go to** your app name.
3. Tick every box, then click **Continue**.

The token goes to `secrets/token.json`.

## Problems

| You see | Do this |
|---|---|
| `Google login expired` | In [Audience](https://console.developers.google.com/auth/audience), check that the status is **In production**. Then log in again. |
| `Not granted: ...` or `Token lacks scope: ...` | Log in again and tick every box. |
| `... API has not been used in project ... before or it is disabled` | Enable that API with the link in step 2. Wait a few minutes. |
| `No OAuth client secret at ...` | Do step 4. |
| `Access blocked: ... has not completed the Google verification process` | The status is **Testing** and your account is not a test user. Click **Publish app** in [Audience](https://console.developers.google.com/auth/audience). |

## More

- [Google Auth platform](https://support.google.com/cloud/answer/15544987)
- [Enable Google Workspace APIs](https://developers.google.com/workspace/guides/enable-apis)
