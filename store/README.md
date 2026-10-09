# The Open House Store

Where modules people made are published, found, rated and installed.

## The backend is not written here, and that is deliberate

A store needs accounts, unique names, comments, ratings, counters and a database
to keep them in. Every one of those is a thing that already exists, done well,
and a store that grew its own would be a store whose bugs are all its own. So the
backend is [PocketBase](https://pocketbase.io): one Go binary on SQLite, with a
REST API, record-level auth, unique indexes, an admin UI and a JavaScript hook
runtime already inside it. Standing the Store up is running one file.

What Open House writes is the *vocabulary*: which collection holds a published
module, what a published row looks like, and what a rating is. That lives in
`ha_adapter/store_api.py` -- pure, tested without a server -- and everything in
this directory is the backend's half of that conversation.

**Pinned: PocketBase 0.40.5.** The shapes here (autodate fields declared
explicitly, `_superusers` as the superuser collection, the import endpoint) are
0.40's, and a major version is a thing that moves.

## What is in this directory

| File | What it is |
| --- | --- |
| `collections.json` | The whole schema: five collections, their fields, indexes and rules. This is a PocketBase export, and it imports into an empty database as it is. |
| `pb_hooks/main.pb.js` | Which events move a module's counters. |
| `pb_hooks/store.js` | The arithmetic itself. |
| `docker-compose.yml` | The one command. |
| `.env.example` | The two values the command needs; copied to `.env`. |

## Standing it up

```sh
cd store
cp .env.example .env      # then fill in an admin address and a long password
docker compose up -d
```

The values go in `.env` rather than in front of `up` because compose reads that
file on *every* command, and it refuses all of them while they are unset --
including `docker compose down`. A Store you cannot stop without re-typing your
admin password is one you will leave running.

Then open `http://localhost:8090/_/`, sign in with the two values above, and
import the schema once: **Settings → Import collections → load
`collections.json`**. The alternative is the same thing by hand through the admin
UI, which is what the export exists to avoid.

Two volumes matter: `pb_data` is the whole Store (the database, the uploaded
files, the logs), and `pb_hooks` is this directory. Nothing else needs backing
up, and nothing else needs deleting to start over.

## What the backend enforces

The Store's honesty is in the collection rules, not in the client. `store_api.py`
refuses things too, but only so that a person is told before they are refused --
the rules below are what actually holds.

| Collection | Who may create | Who may change |
| --- | --- | --- |
| `publishers` | anyone (this is a signup) | only themselves |
| `modules` | a signed-in publisher, publishing as themselves | only that publisher |
| `ratings`, `comments`, `installs` | a signed-in publisher, **and not the module's own publisher** | only themselves |

Two indexes do the rest: `idx_publishers_name` makes a publisher name unique
across the Store -- so "please pick another name" is the backend's answer and not
a check the client could forget -- and `idx_ratings_module_publisher` makes one
rating per person per module, so changing your mind is an update rather than a
second opinion.

`stacktrace`-free refusals matter here: a create that fails a rule answers with
the field and the reason, and `store_api.name_taken` reads PocketBase's own
`validation_not_unique` rather than treating every 400 as "the name is gone".

## Why there is a hook at all

A module's rating and its counts are *joins* -- they are answers about three
other collections -- and a Store listing that computed them itself would ask the
database sixty questions to draw one page. So the numbers are kept on the module
row, and the hook is what keeps them true.

The hook is necessary rather than convenient because **the person rating a module
is not the person who published it**, and the `modules` update rule says only a
publisher may edit their own row. That rule is what stops a stranger retitling
somebody else's module, so it stays; the consequence is that the rater cannot be
the one to write `stars_sum`, and the count has to be moved by the server that
accepted the rating. The alternative was a rule loose enough for anyone to edit
any module, which is the one thing a store must not allow.

Both paths recompute the sum from the rows rather than applying a delta: a delta
applied twice -- a retry, two tabs, a hook that ran on the create and the update
-- is a rating of nine out of five, and recomputing cannot drift at all.

### A trap worth knowing about

PocketBase serializes each hook handler and evaluates it in a fresh runtime, so a
function declared at the top of a `.pb.js` file is **not in scope** when the
handler runs -- `require()` inside the handler is the only thing that is. Getting
this wrong does not fail at load: the row is written and the request then fails
with a `ReferenceError`, which is why the arithmetic lives in `store.js` and every
handler loads it itself.

A hooks file is also reloaded on change, but not reliably through a bind mount on
Docker Desktop for Windows -- the file inside the container is new while the
running server still holds the old one. Restart the container after editing hooks.

## What has been verified against a real server

`check.py` walks exactly what the integration will do, against a running
PocketBase, and every reply is parsed by `ha_adapter/store_api.py`. Run it
against a Store somebody has just stood up:

```sh
python store/check.py http://127.0.0.1:8090
```

It is repeatable, and it needs two publishers because a rating is one person's
opinion of somebody else's module. What it checks:

- a module publishes, and appears in a listing with its publisher named
- the published document comes back out as the module definition it was
- a stranger rates it; the publisher rating their own module is refused; rating
  twice is refused by the unique index
- a comment and an install are accepted, and an install twice is refused
- the counters on the module read `4 1 1 1` and the rating `4.0`

The same walk passes on a database emptied and then restored from
`collections.json` alone, which is what makes the export in this directory an
artefact rather than a description.
