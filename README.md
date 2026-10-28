# Aula Connect

A Home Assistant integration for [Aula](https://www.aula.dk), the Danish school and institution
platform. Log in with MitID, and each child gets:

- a presence sensor (checked in, checked out, sick, on holiday) and the times of the day,
- binary sensors for unread messages, notifications, gallery and calendar events,
- calendars of the school's events, weekly plans (Meebook, EasyIQ, MinUddannelse), birthdays
  and newsletters.

Aula itself is reached through the library [aula_connect](https://github.com/HairingX/aula_connect).

## Installation

Through HACS, as a custom repository:

1. In HACS, add `https://github.com/HairingX/ha_aula_connect` as a custom repository of the
   type Integration.
2. Install Aula Connect, and restart Home Assistant.
3. Add the integration under Settings -> Devices & services, and approve the login in the MitID
   app.

### Coming from the Aula integration

An installation of the Aula integration (`aula`) is shown as discovered under Settings ->
Devices & services. Confirm it, and its devices and entities move over to Aula Connect with
their entity ids - and with them their history, dashboards and automations; the MitID login
comes along, so no new login is needed. Then uninstall the old Aula integration in HACS.

To remove the integration, delete it under Settings -> Devices & services, then remove it in
HACS and restart Home Assistant.

## Origin and licence

The integration comes from [HairingX/aula](https://github.com/HairingX/aula), a fork of
[scaarup/aula](https://github.com/scaarup/aula), and is licensed as it is: GNU GPL v3.0 or later
(`LICENSE`).
