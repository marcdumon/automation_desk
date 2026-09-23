# automation_desk

A local app for automating Google Calendar, Google Tasks and Gmail by typing plain sentences.

It is organised in **task groups** (one page each: Calendar, Tasks, Gmail, more later). Each group has **standard tasks**.
Type a sentence in a group's command box, or pick one of its standard tasks first. The app shows exactly what will change,
with a tick box per item, and changes nothing until you press Apply.

| Group | Standard task | Example |
|---|---|---|
| Calendar | Add events from a web page | `add all events from https://…/agenda to calendar Exhibitions except the ones on fridays` |
| Calendar | Delete events from a calendar | `delete the events of next week in calendar Test` |
| Tasks | Change the date of tasks | `change the date of the Zalando and clean tasks in list Today to tomorrow` |
| Tasks | Move tasks to another list | `move all completed tasks to list Completed` |
| Tasks | Complete tasks / Delete tasks / Add a task | `mark the Zalando task as done` |
| Gmail | Find a mail (read only, any language) | `wanneer is het resultaat van de bloedtest beschikbaar, label Health` |
| Gmail | Label or mark mail (also archive) | `archive all newsletters older than 2 weeks` |
| Gmail | Trash mail | `trash all mail from Zalando older than a month` |
| Gmail | Draft an email (never sends) | `draft a mail to Anna: ik ben morgen 10 minuten te laat` |

## What the language model may and may not do

A cheap model on OpenRouter (see `automation_desk.toml`) only fills in a standard task's arguments from your sentence.
It never sees your mail, calendars or tasks: list and calendar names are matched in code.

It never writes a date either. Dates are relative expressions (`tomorrow`, `today+2`, `friday`, `next week`) that
`dates.py` resolves. Numeric dates are rejected, and an explicit one like `october 3` is only accepted when you typed it.

On agenda pages, dates come from schema.org event data or `.ics` files when the page has them. Otherwise code finds
and numbers every date on the page, and the model only points at those numbers.

## Jobs and costs

Every command is a **job** (its preview and, when confirmed, its apply), stored in the SQLite file `data/automation.db` (gitignored). A job records:
- every model call: purpose, the model asked for, the model and provider that answered, tokens in and out, the exact
  cost reported by OpenRouter, time taken, generation id, and the full prompt and reply (failed and retried calls included)
- every Google API call: method, parameters, time taken, error
- every web page read: address, status, size, time taken

Each group page shows the cost line of its own jobs and lists them. The **Jobs & costs** page lists all jobs, with
totals per group and per model. Click any job to see everything it did.

## Web pages that refuse programs

A page is first downloaded directly. When the site refuses that (a bot check, 401, 403, 429, 503), or the page shows no
dates because it builds its agenda with JavaScript, the app reads it through **your own browser**, the one the
Automation desk is open in:
- The Automation desk extension (`browser_extension/`) opens the page in a background tab and waits for it.
- It hands the finished page to the app and closes the tab.
- If the site insists on a "verify you are human" check, the tab comes to the front. You complete it; the extension
  never clicks it.

The app never drives a browser itself, so sites see an ordinary visit of yours.

Install the extension once in Vivaldi (or any Chromium-based browser):
1. Open `vivaldi://extensions` and switch on **Developer mode**.
2. Click **Load unpacked** and choose the project's `browser_extension` folder.

Keep the Automation desk page open while a preview runs; it passes the app's page requests to the extension.

## Run it

```bash
./run_automation_desk.sh          # or add --no-browser
```

It installs missing packages, rebuilds the GUI when its source changed, starts the app, waits until it answers and
opens http://127.0.0.1:8765. If the app is already running it only opens the browser. Ctrl+C stops it; the app's
output goes to `data/server.log`.

## Setup

```bash
uv sync
cp .env.example .env                                  # set OPENROUTER_API_KEY
uv run python -m automation_desk.google_auth           # one-time Google login (Tasks, Calendar, Gmail)
cd frontend && npm install && npm run build && cd ..
uv run automation-desk                                 # http://127.0.0.1:8765
```

The Google login needs an OAuth client of type Desktop at `secrets/client_secret.json` in this project, and stores
its token next to it in `secrets/token.json`. The `secrets/` folder is gitignored. Tasks, Calendar and Gmail APIs must be enabled in that Cloud project.

For frontend development, run `npm run dev` in `frontend/`. It proxies `/api` to port 8765.

## Adding a standard task

Write one class in `src/automation_desk/groups/<group>/tasks/`, subclassing `StandardTask`:
- `Args`: a pydantic model extending `TaskArgs`; any date field is a string holding a relative expression
- `resolve`: reads Google and returns the preview plus a frozen payload
- `execute`: applies the payload to the ticked rows

Then add an instance to the `tasks` list of that group's `TaskGroup` in its `__init__.py`. The GUI needs no change.
A new group is a new package plus one line in `groups/__init__.py`.

## Tests

`uv run pytest` runs without network access, API keys or Google.

## Known limits

- Google Tasks stores only the date of a due date, and recurring tasks cannot be moved between lists.
