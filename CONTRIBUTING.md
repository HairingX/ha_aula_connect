# Reporting a problem

Thank you for helping Aula Connect keep up with Aula. Aula changes without notice, and the
integration learns what changed only from what its users send.

1. **Search first.** Many users meet the same change on the same day. Look through the
   [open issues](https://github.com/HairingX/ha_aula_connect/issues?q=is%3Aissue+is%3Aopen) for
   the notice or the problem you see. If it is there, add a thumbs-up, and a comment only when
   you have something it does not say yet.
2. **Pick the form that fits.** [New issue](https://github.com/HairingX/ha_aula_connect/issues/new/choose)
   offers one form for a repair notice from Aula Connect, and one for any other problem. Each
   asks for what is needed to help.
3. **One issue per change or problem.** Writing in Danish is fine.

## The repair notices

Aula Connect raises a notice under Settings > System > Repairs when Aula sends something it does
not know. A notice goes away by itself once Aula has not sent it again for a day. Each needs the
diagnostics to be fixed: see [Downloading the diagnostics](#downloading-the-diagnostics).

### Aula's API has a new version

Aula has moved its API on to a version Aula Connect was not made for. Aula Connect follows it,
and most often all works as before. Search the issues for the version the notice names, such as
[`API version 25`](https://github.com/HairingX/ha_aula_connect/issues?q=is%3Aissue+%22API+version%22):
one report per version is enough. Report it with the form **Aula sends something new**, and
attach the diagnostics.

### Aula sends notifications Aula Connect does not know

Aula sent notifications of an area Aula Connect does not know yet; the notice names it. Search
the issues for that name, and report it with the form **Aula sends something new**, with the
diagnostics attached, so that Aula Connect can learn it.

### Aula Connect could not read an answer from Aula

Aula answered in a form Aula Connect could not read; the notice names what it was reading, such
as "messages". Something is most likely missing in Home Assistant until it is fixed. Search the
issues for what the notice names, and report it with the form **Aula sends something new**, with
the diagnostics attached.

## Downloading the diagnostics

1. In Home Assistant, go to Settings > Devices & services > Aula Connect.
2. Open the menu (⋮) of the entry, and choose **Download diagnostics**.
3. Read the file through before you share it, as below.
4. Attach the file to the issue: drag it into the field the form names, or tap the field and
   choose the file.

The diagnostics read Aula once more and hold what it answers. The MitID user ID and every token
are hidden. Every id, name, e-mail address, phone number, address and link Aula Connect knows is
replaced by another of the same length, the same each time, so that what belongs together still
does. A name Aula writes inside a text without saying it is a name can remain, such as another
child's name in a message. An issue is public: read the file through, and do not share it if it
holds what you do not want to share.

## Any other problem

Use the form **Problem report**. It asks for the versions, what happened, and the log of Aula
Connect; the diagnostics help with most problems too.

## Ideas

Ideas are welcome as an issue with the form **Problem report**, saying what you would like
Aula Connect to do.
