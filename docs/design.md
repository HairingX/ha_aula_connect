# How Aula Connect is built

Aula Connect turns an Aula login into Home Assistant entities. It talks neither to MitID nor to
Aula itself:

- [mitid_connect](https://github.com/HairingX/mitid_connect) logs in through MitID.
- [aula_connect](https://github.com/HairingX/aula_connect) logs in to Aula with MitID's answer,
  keeps the tokens fresh, and reads Aula's API into typed models. Aula's endpoints and models are
  documented there.
- This repository holds the config flow, the login page, the coordinators and the entities.

The libraries are asynchronous, on aiohttp. Each entry, and each login of the setup, gets a
session of its own from `async_create_clientsession`, with a cookie jar of its own: Aula's and
MitID's cookies are the login's, and must not mix with other integrations' or other logins'.
Home Assistant lets go of an entry's session when the entry is unloaded; a login lets go of its
own when it ends, or when the dialog is closed, which also stops it.

## The entry

An entry is one MitID account. Its unique id is the SHA-256 of the MitID user ID, as the user ID
is a credential and the unique id is shown in places a credential is not. The entry holds:

- the MitID user ID, needed to log in again;
- the access token, the refresh token and when the access token expires;
- `entity_prefix`, the name the user gave the entry, which starts every entity's unique id.

The unique ids keep the name, not the account, so that an entry made unique per account later
(version 2.2) kept its entities.

## Logging in

MitID offers no way for another application to approve a login, so Aula Connect runs MitID's
browser client and shows its QR code on a page of its own:

1. The config flow asks for the name and the MitID user ID, and starts the login in the
   background: an `AuthSession` per flow, holding the library's login client and how far it has
   come.
2. The flow's external step opens `/api/aula_connect/auth/<flow id>`. The frontend opens only an
   absolute http(s) address, and Home Assistant's own URLs may be ones the browser cannot reach,
   so the address is the one the browser used to start the flow.
3. The page asks `/status` once a second for the step, the QR code (two, shown in turn) and
   whether the login completed or failed; `/select_identity` passes the identity chosen when
   MitID offers several. The pages need no Home Assistant token, as they are opened in a new tab:
   the flow id in their address is known only to whoever started the flow.
4. When the login ends, the flow goes on by itself: to the entry with the tokens, or to a form
   that offers to try again. A completed login is kept a minute after the flow ends, so that its
   page sees it complete; one that did not complete is dropped at once.

Logging in again (reauth) and changing the user ID (reconfigure) run the same login and update
the entry in place.

## Tokens

The library refreshes the access token when it expires, and hands the new tokens to a callback
that stores them in the entry. When Aula refuses the refresh token (`invalid_grant`), the
library raises `AulaCredentialError`; the coordinators turn it into `ConfigEntryAuthFailed`, and
Home Assistant asks the user to log in again. A calendar reading a period outside the two weeks
does so outside a refresh, so it starts logging in again itself.

Neither a token nor the MitID user ID is logged or in the diagnostics. Aula takes the access token
in the URL, and the HTTP client puts the URL in its errors, so every error text is redacted before
it is logged or shown, and the coordinators raise their own errors unchained from the client's.

## Reading Aula

Two coordinators read Aula, both first while the entry is set up, so that an Aula out of reach
retries the setup:

- **The data coordinator**, every 5 minutes: the profiles and children, the daily overviews
  (presence), the message threads and the notifications.
- **The calendar coordinator**, every 10 minutes: birthdays, events, weekly plans, EasyIQ plans
  and newsletters, two weeks ahead. Each calendar registers what it needs - an institution or a
  child - and each kind is read again only when it is older than its interval: a day for
  birthdays, two hours for the rest. A calendar is redrawn only when its own data was read.
  Periods outside the two weeks are read from Aula when the calendar asks for them.

A part of Aula that cannot be read leaves the data from before in place; the log says so once,
and once more when Aula can be read in full again. Three such reads in a row, or a login that
fails, make the entities unavailable.

Which calendars an institution has follows the widgets Aula lists for it: weekly plans and
birthdays with the weekly plan widget, EasyIQ with EasyIQ's, newsletters with MinUddannelse's.

## Entities

- Three sensors per child (status, presence, minutes present), from the daily overview.
- Five binary sensors per entry, for what is unread, from the message threads and notifications.
- An events calendar per child and per parent for each institution, and the other calendars per
  child.

Names come from the translations, with the child's first name and the institution's name as
placeholders. A few calendar details are Aula's rather than Home Assistant's:

- A parents' meeting runs over days while parents book a time; once booked, it shows only the
  booked time, and until then is marked `<!>`.
- A meeting without the child is not in the child's calendar.
- A birthday is shown in every year of the period asked for, on 28 February outside a leap year
  for one born on 29 February.

## The card's API

A dashboard card shows each child's weeks through two websocket commands. They only read: they
mark nothing read and write nothing to Aula. Any user may call them, as a dashboard's viewers
need not be administrators; they send no token, no MitID user ID and no UNI-login.

- `aula_connect/children` answers with the children of every loaded entry, each with its
  entry's id and the id its calendars are kept under (`child_id`), its names and its
  institution's name.
- `aula_connect/week/subscribe` takes an entry, a child and a period of 1 to 21 days (`start`
  included, `end` not), answers, and then sends the period's lessons, other events, Meebook
  tasks and birthdays, each sorted by time. A malformed period is refused as `invalid_format`, an
  entry or child not loaded as `not_found`.

The days are chosen as the child's calendars choose them: lessons and events as the child's
events calendar (a meeting without the child left out, a booked meeting at its booked time),
tasks and birthdays only where the weekly plan widget is, and a birthday on 29 February on 28
February outside a leap year. What the calendar coordinator last read covers the days it was
read for; days outside them are read from Aula, as a calendar reads a period: a part that cannot
be read is left out and logged, and a login Aula no longer accepts starts logging in again. The
days are sent again each time the coordinator reads the child's events, weekly plans or
birthdays anew - not at every poll, which mostly reads nothing - until the subscription ends.
When the entry is unloaded (as it is to reload it or remove it), or Aula no longer lists the
child, the subscription sends `{"ended": true}` once and sends nothing more; unsubscribing
afterwards is still answered as a success. The card then asks for the children again and
subscribes anew.
A Meebook task's content is sent as Meebook has it, HTML included. Times are ISO 8601 with
their offset; an all-day event is sent as the local days it covers, its `end` the day after the
last. A lesson's `teachers` are full names, and its `status` is Aula's own (`normal` and
`substitute` are the ones known). `week_plan` says whether the institution has the weekly plan
widget, without which there are no tasks or birthdays to send.

## Tests

The tests set up entries against a made-up Aula account (`tests/fake_aula.py`), a stand-in for
the library's client, and read the entities through Home Assistant: their states, and the
calendars through the `calendar.get_events` action. The login flow runs with a stand-in MitID
client. One test runs the whole way on the real libraries: the setup's MitID login through the
library's simulated Aula login chain (`aula_connect.testing`, with `mitid_connect`'s simulated
MitID behind it), and the entities read from its simulated API. A test checks that the README
names every entity and attribute there is, and no other.
