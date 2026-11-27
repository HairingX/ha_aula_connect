# Aula Connect

A Home Assistant integration for [Aula](https://www.aula.dk), the platform Danish schools,
after-school clubs and day care use to talk with parents: presence, messages, calendars and
weekly plans. Aula Connect logs in to Aula with your MitID, as you do on aula.dk, and shows
what Aula says about your children in Home Assistant:

- whether each child is checked in, checked out, sick or on a trip, and since when;
- whether there are unread messages, posts, calendar events, photos or presence requests, and
  an event for each new one;
- calendars of the school's events, the weekly plans, the class's birthdays and the newsletters;
- each child's picture, what its weekly plan marks as important as a to-do list, whether it has school today and
  tomorrow, when its school starts and ends, and the vacation requests waiting for an answer.

On a user's explicit action - an action called, or a tap in the Aula Connect cards - it can also
change things in Aula: report a child sick, register or delete a vacation, answer a vacation
request, and reply in a thread to the teachers. See [Changing things in Aula](#changing-things-in-aula).

Aula itself, the login and the API, is reached through the library
[aula_connect](https://github.com/HairingX/aula_connect).

## Use cases

- Get a notification on your phone when a child is checked out of after-school care.
- See on a wall tablet who is at school and who is at home.
- Show tomorrow's weekly plan, and what is important for it, on the family dashboard in the evening.
- Get told when a new message or post arrives in Aula, without the Aula app.

## Requirements

- A parent login to Aula through MitID, with the MitID app on a phone.
- Home Assistant 2026.11.0 or later, with HACS.

## Installation

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=HairingX&repository=ha_aula_connect&category=integration)

1. In HACS, add `https://github.com/HairingX/ha_aula_connect` as a custom repository of the
   type Integration, or use the button above.
2. Download Aula Connect, and restart Home Assistant.
3. Under **Settings > Devices & services**, select **Add integration** and choose
   **Aula Connect**.
4. Fill in the two fields below and select **Next**. A page with a QR code opens; if it does not,
   use the link in the dialog.
5. In the MitID app on your phone, scan the QR code and approve. If your MitID has more than one
   identity, choose it on the page.

The QR code must be scanned from another screen than the phone with the MitID app on it: open
the setup on a computer or tablet, or open the link on one.

### Setup fields

| Field | What it is |
|-------|------------|
| Name | What this Aula login is called in Home Assistant, `Aula` unless you change it. The entity ids start with it, such as `sensor.aula_alma_school_presence`. Each login needs its own name. |
| MitID user ID | The user ID you log in to MitID with. It is used only to start the MitID login. |

Each MitID account can be set up once. Two parents with their own MitID can each set up their
own login under different names.

### Options

Under **Settings** > **Devices & services** > **Aula Connect** > **Configure**, each kind of change
in Aula is turned on or off for the login. Each is off until you turn it on; what is off, neither
the cards, the actions nor an AI assistant using Home Assistant can do, and the cards show no
button for it. Reading Aula is always on.

| Option | What it allows |
|--------|----------------|
| Report sick | Reporting a child sick today, and taking it back where that can be done. At some institutions a sick report cannot be taken back. |
| Register and delete vacations | Registering a vacation for the children, and deleting one. |
| Answer vacation requests | Answering an institution's vacation request, day by day. |
| Reply in threads | Replying in a thread about a child, to everyone in it. A new thread is never started. |

The login is reloaded when the options change.

### Coming from the Aula integration

Aula Connect is a new integration under the domain `aula_connect`, and does not take anything
over from the Aula integration (`aula`). To change over:

1. Delete the Aula integration under **Settings > Devices & services**, and remove it in HACS.
2. Set up Aula Connect as above, with the name `Aula`. The login's entities then get the same
   entity ids as the Aula integration's had. A child's entities are named after the child's
   device, such as `sensor.aula_alma_school_presence` where the Aula integration had
   `sensor.aula_presence_alma_school`; to keep a dashboard or an automation as it is, give the
   entity its old id under **Settings > Entities**.

## Entities

Every login gets a device, named as the login, and under it a device for each child at each
institution Aula lists it at, named by the child's first name and the institution's name, such
as `Alma · School`. A child at a school and an after-school club has two devices. The entities of
a child at an institution are on its device, and the login's own on the login's.
In the entity ids below, `<child>` is the child's first name and `<institution>` the
institution's name, as Aula has them; an installation set up before the children had devices
keeps the entity ids it had, such as `sensor.aula_presence_alma_school`.
Its list of homework is now each child's list of what is important: its word in the entity id
becomes `important` (`vigtigt` for one named in Danish), such as
`todo.aula_important_alma_school`, where no other entity has that id.

To leave a child at an institution out - one it no longer attends, or one you do not want in
Home Assistant - open **Settings > Devices & services > Aula Connect**, select the device, and
select **Disable**. A disabled device is not read from Aula at all: none of its entities, no
presence, calendars or picture, and the child at that institution is not in the cards or the
actions. **Enable** brings it back. A child Aula lists at a new institution gets its device and
entities by itself, at the next read. When Aula no longer lists a child at an institution, its
device is removed with its entities; a disabled one is kept until you delete it, which is
allowed once Aula no longer lists it.

### For each child

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.aula_<child>_<institution>_status` | What Aula says about the child today: Not present, Sick, Reported absent, Present, Field trip, Sleeping, Spare time activity, Present at location or Checked out. | `check_in_time`, `check_out_time`, `check_in_time_expected`, `check_out_time_expected`, `exit_with`, `institution_name`, `location_description`, `location_icon`, `location_name`, `main_group` |
| `sensor.aula_<child>_<institution>_presence` | Present while the child is at the institution, on a field trip, asleep or at a location; Not present before arriving, when sick or reported absent, at a spare time activity and when checked out; Unknown otherwise. | `check_in_time`, `check_out_time`, `check_in_time_expected`, `check_out_time_expected`, `exit_with`, `institution_name`, `main_group` |
| `sensor.aula_<child>_<institution>_present_duration` | Minutes the child has been checked in today. | `check_in_time`, `check_out_time`, `main_group` |
| `calendar.aula_<child>_<institution>_events` | The institution's calendar: lessons with teachers and substitutes, events, holidays and booked meetings. | |
| `calendar.aula_<child>_<institution>_weekly_plans` | The weekly plan's tasks and comments, one all-day event each. | |
| `calendar.aula_<child>_<institution>_birthdays` | The birthdays Aula ties to the child, such as its class's, with the age each turns. | |
| `calendar.aula_<child>_<institution>_easyiq_weekly_plans` | The EasyIQ weekly plan. | |
| `calendar.aula_<child>_<institution>_newsletter` | The MinUddannelse newsletters, one for each week, with their HTML as the description. | |
| `image.aula_<child>_<institution>_picture` | The child's profile picture in Aula, read anew when it is shown, as Aula's link to it expires within the hour; the time it last changed. Aula's link is never an attribute. | `child_id` |
| `todo.aula_<child>_<institution>_important` | What the weekly plan marks as important - its tasks: homework, things to bring, things to decide - each due on its day: the number not ticked. Ticking one is remembered in Home Assistant only, never told to Aula; a task cannot be added, renamed or removed, as the plan is the teachers'. | |
| `binary_sensor.aula_<child>_<institution>_school_today` | On when the child has lessons today; off on a day without, and on a holiday. | `start`, `end` |
| `binary_sensor.aula_<child>_<institution>_school_tomorrow` | The same for tomorrow. | `start`, `end` |
| `sensor.aula_<child>_<institution>_school_start` | When the child's first lesson today starts; unknown on a day off. | |
| `sensor.aula_<child>_<institution>_school_end` | When the child's last lesson today ends; unknown on a day off. | |
| `binary_sensor.aula_<child>_<institution>_vacation_request` | On while the institution waits for an answer to a vacation request that can still be answered: `count` of them, and the first `deadline`. | `count`, `deadline` |

The weekly plan and birthday calendars and the list of what is important come with an institution that has
weekly plans in Aula, the EasyIQ calendar with one that has EasyIQ's weekly plans, and the
newsletter calendar with one that has MinUddannelse's newsletters.

A school day is told from the lessons in the institution's calendar; a day with a holiday in it
counts as a day off. The day moves on within 10 minutes of midnight.

### For each parent

| Entity | State | Attributes |
|--------|-------|------------|
| `calendar.aula_events_<parent>_<institution>` | The institution's calendar as the parent sees it, such as parents' meetings. | |

### For the login

| Entity | On when | Attributes |
|--------|---------|------------|
| `binary_sensor.aula_unread_message` | A message thread is unread. | `total`, `subject`, `text`, `timestamp`, `recipients`, `sensitive`, `muted`, `marked` |
| `binary_sensor.aula_unread_post` | A post is unread. | `total`, `alert`, `title` |
| `binary_sensor.aula_unread_calendar` | A calendar event is unread. | `total`, `alert`, `title`, `all_day`, `start_datetime`, `end_datetime` |
| `binary_sensor.aula_unread_gallery` | Photos or albums are unseen. | `total` |
| `binary_sensor.aula_unread_presence` | A presence request, such as a holiday request, is unanswered. | `total`, `alert`, `title`, `text`, `start_datetime`, `end_datetime` |
| `event.aula_new_message` | Fires for a new message in an unread thread: its thread, subject and the children it is about, never its text or sender. | `event_type`, `thread_id`, `subject`, `sensitive`, `children` |
| `event.aula_new_photo` | Fires for new photos or videos Aula notifies, in the gallery or an album. | `event_type`, `album_id`, `count` |
| `event.aula_new_event` | Fires for a calendar event Aula notifies, such as an invitation. | `event_type`, `event_id`, `title`, `start`, `end`, `all_day` |

The attributes of the unread sensors describe the first unread item. A sensitive message shows
no subject or text; read it in Aula.

An event fires when a read of Aula finds what the read before it did not have; what is there
when Home Assistant starts counts as known. A reply sent from Home Assistant is no new message.

## How the data is updated

Aula Connect asks Aula; Aula does not tell it when something changes.

- Presence, messages, notifications and vacation requests are read every 5 minutes; the way each
  child is reported sick, once an hour.
- The calendars are looked at every 10 minutes. Events, weekly plans, EasyIQ plans and
  newsletters are read again every 2 hours, birthdays once a day, two weeks ahead.
- Looking further ahead or back in a calendar asks Aula for that period there and then.

A single read that fails does not change anything: Aula Connect waits 10 seconds and reads that
part once more - the login, presence, messages, notifications, or one kind of calendar, such as
the birthdays. Only when that fails too do the entities that show it become unavailable, and the
others stay as they are; the same holds for a child Aula gives no presence for. The log says once
that Aula could be read only in part, and once that it can be read in full again; the
unavailable entities show it for as long as it lasts.

## Examples

A notification when a child is checked out:

```yaml
automation:
  - alias: "Alma is checked out"
    triggers:
      - trigger: state
        entity_id: sensor.aula_alma_school_status
        to: "8"
    actions:
      - action: notify.notify
        data:
          message: "Alma has been checked out ({{ state_attr('sensor.aula_alma_school_status', 'exit_with') }})."
```

A notification when a message arrives in Aula:

```yaml
automation:
  - alias: "New message in Aula"
    triggers:
      - trigger: state
        entity_id: binary_sensor.aula_unread_message
        to: "on"
    actions:
      - action: notify.notify
        data:
          title: "Aula"
          message: "{{ state_attr('binary_sensor.aula_unread_message', 'subject') }}"
```

Tomorrow's weekly plan, read in a script:

```yaml
script:
  tomorrows_plan:
    sequence:
      - action: calendar.get_events
        target:
          entity_id: calendar.aula_alma_school_weekly_plans
        data:
          start_date_time: "{{ (today_at() + timedelta(days=1)).isoformat() }}"
          duration:
            days: 1
        response_variable: plan
      - action: notify.notify
        data:
          message: >-
            {{ plan['calendar.aula_alma_school_weekly_plans'].events
               | map(attribute='summary') | join('\n') }}
```

The calendars show in Home Assistant's own calendar, and on a dashboard with the calendar card:

```yaml
type: calendar
entities:
  - calendar.aula_alma_school_events
  - calendar.aula_alma_school_weekly_plans
  - calendar.aula_alma_school_birthdays
```

## Changing and logging in again

- When Aula no longer accepts the login, as happens when it has not been used for a long time,
  Home Assistant says the login has expired. Select **Reconfigure** on the notice and approve
  with MitID again; the entities stay as they are.
- To log in with another MitID user ID, select **Reconfigure** on the integration's entry.

## Known limitations

- Aula has no public API. Aula Connect reads what aula.dk shows, and can stop working when Aula
  changes; report it under [issues](https://github.com/HairingX/ha_aula_connect/issues).
- The login is approved only by scanning a QR code with the MitID app. MitID code displays,
  code readers and approving in the app on the phone that shows the page are not supported.
- The texts Aula Connect adds to calendar events, such as "Vikar" and "fylder", are in Danish.
- Aula reports two presence states whose meaning is not known; the status sensor then shows 9
  or 10.
- What is sent to Aula is limited to the actions below. A new message cannot be started, only a
  reply in a thread there is; pick-up times, check-in and check-out cannot be registered.
- A child reported sick through IST's Tabulex cannot be reported well again from Home
  Assistant: Tabulex takes no report back. It is found in Tabulex by its whole name, else its
  first name, and only at the institution Aula has it at; elsewhere it is not reported.
- Whether the family wrote to the teachers yesterday is known only of replies sent through Home
  Assistant, and a school day there is any weekday.
- Photos are not in Home Assistant's media browser yet.

## Changing things in Aula

> **None of these has been tried against the real Aula yet.** They are built from what Aula's own
> web portal sends, and tested against a simulated Aula. Try each once, on a date you choose -
> for example a vacation far ahead that you then delete - before relying on it.

Aula Connect changes something in Aula only where its [options](#options) allow that kind of
change, and only when a user asks for it: an action called by hand,
by a script or an automation, or a tap in the Aula Connect cards, which ask for confirmation
first. Nothing is sent by the regular reads. Each is sent once and never again by itself: where
it fails, Aula may or may not have done it, and the error says to look in Aula before trying
again. It is done as the guardian whose MitID the entry logged in with. Any user of Home
Assistant may call them, as with Home Assistant's other actions.

| Action | What it does in Aula |
|--------|----------------------|
| `aula_connect.report_sick` | Reports the child sick today at the institution Aula has it at, the way that institution lets guardians: in Aula's presence module (Komme/gå), or through IST's Tabulex where its Aula widget is how the institution takes sick reports. Responds with the way it was reported. |
| `aula_connect.undo_sick` | Takes back today's sick report. |
| `aula_connect.check_sick_report` | Sends nothing: tells what `report_sick` would do - the route, whether the child is found there (in Tabulex, by its name), whether it is reported sick there today already, and the request it would send. It works whatever the options allow. |
| `aula_connect.add_vacation` | Registers a vacation or time off, whole days from `start` to `end`: the start today or later, the end at most a year after it, a comment of at most 250 characters. |
| `aula_connect.delete_vacation` | Deletes a vacation registered for one of the children, where Aula lets it be changed. |
| `aula_connect.answer_vacation_request` | Answers an institution's vacation request: for each weekday from today, whether the child comes, with entry and exit times where the request asks for them. |
| `aula_connect.send_message` | Replies in one of the child's threads, to everyone in it, as plain text. Nothing is sent without a thread. |

Each takes `config_entry_id`, the entry of the guardian it is done as, and the child's id,
`child_id`, which is the attribute `child_id` of the child's picture entity. Where the
institution takes sick reports in IST's Tabulex, `report_sick` reports the child sick there, for
today, through the login's widget into Tabulex, and it cannot be taken back; `undo_sick` says so.
Where it takes them not from guardians at all, `report_sick` says so and sends nothing. Each
responds with the route it took: `aula` or `tabulex`.

```yaml
action: aula_connect.report_sick
data:
  config_entry_id: <the Aula Connect entry>
  child_id: "{{ state_attr('image.aula_alma_school_picture', 'child_id') }}"
```

```yaml
action: aula_connect.add_vacation
data:
  config_entry_id: <the Aula Connect entry>
  child_ids: ["{{ state_attr('image.aula_alma_school_picture', 'child_id') }}"]
  start: "2027-07-05"
  end: "2027-07-23"
  comment: Sommerferie
```

What a sick report needs to remember - the days each child was sick, as reported through Home
Assistant or seen in the presence read every 5 minutes, the thread the family last wrote to the
teachers in, and the days it wrote - is kept in Home Assistant's storage for 60 days, and
deleted with the entry. So is what is important ticked.

## Actions

**Probe widget services** (`aula_connect.probe_widgets`) learns, in one run, how each Aula widget
with a login of its own - such as IST's into Tabulex - logs the guardian in, and what its service
loads and answers, to learn how a child could be reported sick there. It follows each login as a
browser does, but never into Aula's, MitID's or UniLogin's own logins; reads the scripts of the
page it ends at; and reads with GET the addresses the service's own code names, but never one
whose name tells of a change, such as delete, save, send or report. With `reads`, it also reads
the addresses given. It responds with how each step, page and answer is built - host, path,
status, the names of fields and cookies, a page's title and headings, the scripts' text, and
each JSON answer by its shape and pseudonymised as the diagnostics pseudonymise Aula's - never a
value of a token, cookie or field. It posts nothing but each login's own forms. Only an
administrator may use it. The diagnostics hold its latest report.

**Read from Aula** (`aula_connect.read`) reads one method of Aula's API and answers as the
diagnostics do: every id, name, contact detail and link replaced by another of the same length,
and no token. Only an administrator may use it. Use it to see what Aula sends where the
integration does not read it yet. The method is chosen from those known to exist, listed with their
parameters in the library's [catalog of Aula's methods](https://github.com/HairingX/aula_connect/blob/main/docs/aula-methods.md),
or typed as Aula spells it; an unlisted one is read where its name starts with `get` or `find`
after its area. It never reads a method that changes Aula or hands out a token, nor the text of
messages or a secure document. `reveal` keeps the values under the keys it
names as Aula sent them, such as `["widgetCategory"]` for a field of a module not known yet, or the
whole answer with `true`; what is revealed is no longer anonymous, so share it only in private.
The MitID user ID and every token stay hidden. The method is asked with GET; with `body`, it is
asked with POST and the body as JSON, as Aula asks some of the methods that get, such as the
calendar's events. A known method asked by POST needs a body:

```yaml
action: aula_connect.read
data:
  config_entry_id: <the Aula Connect entry>
  method: profiles.getProfileContext
  params:
    portalrole: guardian
response_variable: answer
```

```yaml
action: aula_connect.read
data:
  config_entry_id: <the Aula Connect entry>
  method: calendar.getEventsByProfileIdsAndResourceIds
  body:
    instProfileIds: [1234567]
    resourceIds: []
    start: "2026-11-16 00:00:00.0000+01:00"
    end: "2026-11-23 00:00:00.0000+01:00"
  reveal: ["id"]
response_variable: answer
```

## Troubleshooting

- **No page opens after Next.** Use the link in the dialog. Home Assistant must be reached at the
  same address in the browser as the one the setup was started from.
- **The login fails.** Select **Next** on the error to start a new login.
- **The entities are unavailable.** Aula could not be read; the log says why. Aula Connect tries
  again by itself.
- **Aula sends something Aula Connect does not know.** A notice under Settings > System >
  Repairs names what - a notification of an unknown kind, an answer that could not be read, a new
  version of Aula's API, one notice for each - and asks for the diagnostics; its **Learn more**
  leads to [how to report it](CONTRIBUTING.md). A notice goes away by
  itself once Aula has not sent it again for a day, a restart or a reload counted in. A notice
  ignored stays ignored.
- **Something looks wrong.** Download the diagnostics from the integration's entry. They read
  Aula once more, from the start of this week and three weeks on, the birthdays for a year on,
  and hold what it answers - the messages of every thread too, except those marked sensitive,
  and every other method of Aula's that only reads, such as the posts, the gallery, the common
  files and the presence registrations, the first items of each list one by one - with each answer's headers and when
  it came, and the newest failure of each request, an error such as a timeout among them, even
  where it later succeeded. The MitID user ID and every token are hidden wherever they stand. All
  the answers say is replaced, but how they are built: every word of every text, every id, code,
  e-mail address, phone number, address and link gets another of the same length - "Alma" by
  "Pyny", the same each time - so that what belongs together still does, and the text fills as
  much as on the screen. Kept are the keys, the kinds Aula names, such as a message's type or a
  notification's area, HTML's tags, dates, times and numbers that are no ids. Read the file through
  before sharing it all the same. A thread marked sensitive is never read. The secret
  that picks the replacements is kept in Home Assistant only. To see more, turn on debug logging
  under the integration's entry. A debug log can hold your children's data: read it through
  before sharing it.

## Removal

1. Under **Settings > Devices & services**, open **Aula Connect**, and select **Delete** in the
   menu of each login. Its tokens are deleted with it.
2. In HACS, remove Aula Connect, and restart Home Assistant.

## Origin and licence

The integration comes from [HairingX/aula](https://github.com/HairingX/aula), itself changed from
an earlier Home Assistant integration for Aula under the GNU GPL, and is licensed as it is: GNU
GPL v3.0 or later (`LICENSE`); it has been changed since.
