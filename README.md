# Aula Connect

A Home Assistant integration for [Aula](https://www.aula.dk), the platform Danish schools,
after-school clubs and day care use to talk with parents: presence, messages, calendars and
weekly plans. Aula Connect logs in to Aula with your MitID, as you do on aula.dk, and shows
what Aula says about your children in Home Assistant:

- whether each child is checked in, checked out, sick or on a trip, and since when;
- whether there are unread messages, posts, calendar events, photos or presence requests;
- calendars of the school's events, the weekly plans, the class's birthdays and the newsletters.

Aula itself, the login and the API, is reached through the library
[aula_connect](https://github.com/HairingX/aula_connect).

## Use cases

- Get a notification on your phone when a child is checked out of after-school care.
- See on a wall tablet who is at school and who is at home.
- Show tomorrow's weekly plan and homework on the family dashboard in the evening.
- Get told when a new message or post arrives in Aula, without the Aula app.

## Requirements

- A parent login to Aula through MitID, with the MitID app on a phone.
- Home Assistant 2026.10.4 or later, with HACS.

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
| Name | What this Aula login is called in Home Assistant, `Aula` unless you change it. The entity ids start with it, such as `sensor.aula_presence_alma_school`. Each login needs its own name. |
| MitID user ID | The user ID you log in to MitID with. It is used only to start the MitID login. |

Each MitID account can be set up once. Two parents with their own MitID can each set up their
own login under different names.

### Coming from the Aula integration

Aula Connect is a new integration under the domain `aula_connect`, and does not take anything
over from the Aula integration (`aula`). To change over:

1. Delete the Aula integration under **Settings > Devices & services**, and remove it in HACS.
2. Set up Aula Connect as above, with the name `Aula`. Its entities then get the same entity
   ids as the Aula integration's had, so dashboards, automations and history carry on.

## Entities

Every login gets one device, named as the login, with these entities. `<child>` is the child's
first name and `<institution>` the institution's name, as Aula has them.

### For each child

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.aula_status_<child>_<institution>` | What Aula says about the child today: Not present, Sick, Reported absent, Present, Field trip, Sleeping, Spare time activity, Present at location or Checked out. | `check_in_time`, `check_out_time`, `check_in_time_expected`, `check_out_time_expected`, `exit_with`, `institution_name`, `location_description`, `location_icon`, `location_name`, `main_group` |
| `sensor.aula_presence_<child>_<institution>` | Present while the child is at the institution, on a field trip, asleep or at a location; Not present before arriving, when sick or reported absent, at a spare time activity and when checked out; Unknown otherwise. | `check_in_time`, `check_out_time`, `check_in_time_expected`, `check_out_time_expected`, `exit_with`, `institution_name`, `main_group` |
| `sensor.aula_present_duration_<child>_<institution>` | Minutes the child has been checked in today. | `check_in_time`, `check_out_time`, `main_group` |
| `calendar.aula_events_<child>_<institution>` | The institution's calendar: lessons with teachers and substitutes, events, holidays and booked meetings. | |
| `calendar.aula_weekly_plans_<child>_<institution>` | The weekly plan's tasks and comments, one all-day event each. | |
| `calendar.aula_birthdays_<child>_<institution>` | The class's birthdays, with the age each turns. | |
| `calendar.aula_easyiq_weekly_plans_<child>_<institution>` | The EasyIQ weekly plan. | |
| `calendar.aula_newsletter_<child>_<institution>` | The MinUddannelse newsletters, one for each week, with their HTML as the description. | |

The weekly plan and birthday calendars come with an institution that has weekly plans in Aula,
the EasyIQ calendar with one that has EasyIQ's weekly plans, and the newsletter calendar with
one that has MinUddannelse's newsletters.

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

The attributes of the unread sensors describe the first unread item. A sensitive message shows
no subject or text; read it in Aula.

## How the data is updated

Aula Connect asks Aula; Aula does not tell it when something changes.

- Presence, messages and notifications are read every 5 minutes.
- The calendars are looked at every 10 minutes. Events, weekly plans, EasyIQ plans and
  newsletters are read again every 2 hours, birthdays once a day, two weeks ahead.
- Looking further ahead or back in a calendar asks Aula for that period there and then.

When Aula cannot be read at all, the entities become unavailable until it answers again. When
only part of it can be read, the entities keep what they had, and become unavailable only when
that happens three times in a row. The log says once that Aula could be read only in part, and
once that it can be read in full again.

## Examples

A notification when a child is checked out:

```yaml
automation:
  - alias: "Alma is checked out"
    triggers:
      - trigger: state
        entity_id: sensor.aula_status_alma_school
        to: "8"
    actions:
      - action: notify.notify
        data:
          message: "Alma has been checked out ({{ state_attr('sensor.aula_status_alma_school', 'exit_with') }})."
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
          entity_id: calendar.aula_weekly_plans_alma_school
        data:
          start_date_time: "{{ (today_at() + timedelta(days=1)).isoformat() }}"
          duration:
            days: 1
        response_variable: plan
      - action: notify.notify
        data:
          message: >-
            {{ plan['calendar.aula_weekly_plans_alma_school'].events
               | map(attribute='summary') | join('\n') }}
```

The calendars show in Home Assistant's own calendar, and on a dashboard with the calendar card:

```yaml
type: calendar
entities:
  - calendar.aula_events_alma_school
  - calendar.aula_weekly_plans_alma_school
  - calendar.aula_birthdays_alma_school
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
- Nothing is sent to Aula: messages cannot be answered, nor presence registered, from Home
  Assistant.

## Troubleshooting

- **No page opens after Next.** Use the link in the dialog. Home Assistant must be reached at the
  same address in the browser as the one the setup was started from.
- **The login fails.** Select **Next** on the error to start a new login.
- **The entities are unavailable.** Aula could not be read; the log says why. Aula Connect tries
  again by itself.
- **Something looks wrong.** Download the diagnostics from the integration's entry. They hold
  only counts and states: no MitID user ID, no tokens and nothing about your children. To see
  more, turn on debug logging under the integration's entry. A debug log can hold your
  children's data: read it through before sharing it.

## Removal

1. Under **Settings > Devices & services**, open **Aula Connect**, and select **Delete** in the
   menu of each login. Its tokens are deleted with it.
2. In HACS, remove Aula Connect, and restart Home Assistant.

## Origin and licence

The integration comes from [HairingX/aula](https://github.com/HairingX/aula), a fork of
[scaarup/aula](https://github.com/scaarup/aula), and is licensed as it is: GNU GPL v3.0 or later
(`LICENSE`).
