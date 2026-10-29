# How Aula Connect is built

Aula Connect turns an Aula login into Home Assistant entities. It talks neither to MitID nor to
Aula itself:

- [mitid_connect](https://github.com/HairingX/mitid_connect) logs in through MitID.
- [aula_connect](https://github.com/HairingX/aula_connect) logs in to Aula with MitID's answer,
  keeps the tokens fresh, and reads Aula's API into typed models. Aula's endpoints and models are
  documented there.
- This repository holds the config flow, the login page, the coordinators and the entities.

The library is synchronous, built on `requests`; every call to it runs in Home Assistant's
executor.

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
Home Assistant asks the user to log in again.

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

## Tests

The tests set up entries against a made-up Aula account (`tests/fake_aula.py`), a stand-in for
the library's client, and read the entities through Home Assistant: their states, and the
calendars through the `calendar.get_events` action. The login flow runs with a stand-in MitID
client. A test checks that the README names every entity and attribute there is, and no other.
