# automation_desk

A local app for automating Google Calendar, Gmail, News and your Todoist tasks by typing plain sentences.

It is organised in **task groups** (one page each: Calendar, Tasks, Gmail, more later). Each group has **standard tasks**.
Type a sentence in a group's command box, or pick one of its standard tasks first. The app shows exactly what will change,
with a tick box per item, and changes nothing until you press Apply.

The health part (supplements, Withings, Polar H10, sleep, Fitbit) is a separate app, health_dashboard, at
http://127.0.0.1:8766. The Health link in the sidebar opens it.

| Group | Standard task | Example |
|---|---|---|
| Calendar | Add events from a web page or PDF | `add all events from https://…/agenda to calendar Exhibitions except the ones on fridays` |
| Calendar | …or from event details you type or paste (title, place, dates such as 26/9, times) | `add event Expo, Kasteelstraat 3 Antwerpen, za 26/9 - zo 27/9 van 14u tot 22u to calendar Events` |
| Calendar | …from a PDF link, an attached PDF (Attach PDF / drag it in) or a PDF in a mail | `add the events from the PDF in the mail from Kanal to calendar Exhibitions` |
| Calendar | Check watched agenda sites (list on the Calendar page; events not in your calendar; ones you left unticked come back unticked) | `check my agenda sites`, or the Check for new events button |
| Calendar | Delete events from a calendar | `delete the events of next week in calendar Test` |
| Tasks | Move planned tasks out of Someday (Todoist tasks in a Someday section with a this_week, this_month or this_year label go to the top of their project) | `move my planned tasks out of someday` |
| Tasks | Move unplanned tasks into Someday (tasks at the top of a project without a this_ label and without a date go into its Someday section) | `move unplanned tasks back to someday` |
| Tasks | Give tasks a Verb: title (the model proposes 'Clean: Ramen' for 'Ramen poetsen'; every new title can be edited in the preview; the titles go to the model) | `give my tasks a verb: title` |
| Tasks | Give tasks with a label a deadline (a task that has a deadline comes unticked and keeps it unless you tick it; Todoist's deadline, apart from the task's date; label and date found by code) | `give all tasks with label this_week a deadline friday` |
| Gmail | Find a mail (read only, any language) | `wanneer is het resultaat van de bloedtest beschikbaar, label Health` |
| Gmail | Label or mark mail (also archive) | `archive all newsletters older than 2 weeks` |
| Gmail | Trash mail | `trash all mail from Zalando older than a month` |
| Gmail | Draft an email (never sends) | `draft a mail to Anna: ik ben morgen 10 minuten te laat` |
| News | Its own page: sites, subjects and blocked topics as lists, Make digest now, Accept/Block/Reject | — |

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

## News digest

When you press Make digest now, the app reads the news feeds (or front pages) of your sites and shows each new article
as its title with the teaser the site lists for it (at most 60 words); without a teaser, or when it only repeats the
title, just the title. Article pages are never read. The model writes no text: it only sorts the headlines into
your subjects (or suggests a new one) and merges articles about the same story, which keeps a digest fast and cheap.
The News page shows the digest grouped by subject; earlier digests are kept. A daily cost cap (default $0.30) puts the
rest of a day's articles under Other. Article text is not stored.

Blocked topics (e.g. Sports, Showbiz) are offered to the model like subjects; articles filed under one are left out
of the digest and only listed, folded away, under "left out", so a wrongly filed article can still be found.
Subject and topic names are always English; the text under a title stays as the site wrote it.

## Install

The app runs from its own folder, apart from this repo. `install.sh` installs one version (a git tag) from GitHub in a
Python environment in `~/.local/share/md_apps/automation_desk/` (or `$AUTOMATION_DESK_HOME`). It needs uv, npm and curl.

```bash
./install.sh v0.1.0                                          # also to update: an update replaces only env/
~/.local/share/md_apps/automation_desk/env/bin/automation-desk  # http://127.0.0.1:8765
```

The app folder holds the settings: `automation_desk.toml` (the first install makes it from the example), `.env` (set
OPENROUTER_API_KEY and TODOIST_API_TOKEN, see `.env.example`) and `secrets/`. `data_dir` in `automation_desk.toml` sets
the data folder: the database, the uploads, the captures and the logs. Put it where your backups are made; empty, it is
`data/` in the app folder.

The Google login needs an OAuth client of type Desktop at `secrets/client_secret.json` in the app folder, and stores its
token next to it in `secrets/token.json`. The steps for the Cloud project, the APIs, the scopes and the client are in
[docs/google-setup.md](docs/google-setup.md).

## Development

This repo is the app folder of the development copy: its settings, `.env`, `secrets/` and `data/` are next to the code.
Give it its own port in `automation_desk.toml`, so it can run next to the installed app.

```bash
./run_automation_desk.sh          # or add --no-browser
```

It installs missing packages, rebuilds the GUI when its source changed, starts the app, waits until it answers and
opens the browser. If the app is already running it only opens the browser. Ctrl+C stops it; the app's output goes to
`data/server.log`.

The Google code is in the library [google_api](https://github.com/marcdumon/google_api). An install takes it from
GitHub; development uses the folder next to this one:

```bash
git clone https://github.com/marcdumon/google_api ../google_api
uv sync
cp automation_desk.example.toml automation_desk.toml   # your settings (not in git)
cp .env.example .env                                  # set OPENROUTER_API_KEY and TODOIST_API_TOKEN
uv run python -m automation_desk.google_auth           # one-time Google login (Calendar, Gmail)
cd frontend && npm install && npm run build && cd ..
uv run automation-desk
```

For frontend development, run `npm run dev` in `frontend/`. It proxies `/api` to port 18765, the development copy.

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

- Tasks live in Todoist; its lists are Todoist projects. The Todoist API token comes from Todoist → Settings → Integrations → Developer and goes in `.env` as `TODOIST_API_TOKEN`.
