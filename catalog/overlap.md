# Cross-repo overlap

Every behaviour found in two or more reference repos, merged into one row.
Ordered **descending by source-repo count, then ascending by the published
permissiveness order** (`public_domain < mit < apache_2_0 < cc_by_nc_sa <
`no_licence`) -- widest agreement first, and within a tie the most permissive
licence first. `tools/catalog/behaviors.py` re-derives this order from
`catalog/behaviors.yaml` and fails an entry that is missing or out of order.

## notifications.status_alert

- sources: ccostan, fwartner, johnkoht, renemarc
- license: no_licence
- chosen: Raising an alert when a device or service reports a problem.
- rejected: Repo-local spellings in fwartner, johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## climate.zone_setpoint

- sources: ccostan, fwartner, johnkoht
- license: no_licence
- chosen: Driving a climate zone's setpoint from a schedule or presence.
- rejected: Repo-local spellings in fwartner, johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.motion_light_on

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: A motion sensor turning a room's lights on when somebody enters.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.room_light_manual_on

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: A room's lights coming on at a scheduled or mode-driven cue.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## modes.house_night

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: Putting the whole house into its night state.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## modes.room_mode_off

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: A room switched off so its automations stop acting on it.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## security.doorbell

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: Announcing a doorbell press inside the house.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.assorted_housekeeping

- sources: ccostan, fwartner, johnkoht
- license: no_licence
- chosen: A housekeeping automation the others in the corpus do not cover.
- rejected: Repo-local spellings in fwartner, johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.energy_monitoring

- sources: ccostan, fwartner, johnkoht
- license: no_licence
- chosen: Reading energy use and the tariff, and acting on a price signal.
- rejected: Repo-local spellings in fwartner, johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.startup_shutdown

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: Running a routine when Home Assistant starts or stops.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.tag_and_webhook

- sources: ccostan, johnkoht, renemarc
- license: no_licence
- chosen: Reacting to an NFC tag or an inbound webhook.
- rejected: Repo-local spellings in johnkoht, renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.adaptive_colour_temperature

- sources: ccostan, renemarc
- license: apache_2_0
- chosen: Continuously adjusting a light's colour temperature to the time of day.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## security.entry_alert

- sources: ccostan, renemarc
- license: apache_2_0
- chosen: Warning that an entry was opened without a resident arriving.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.time_schedule

- sources: ccostan, renemarc
- license: apache_2_0
- chosen: Running a fixed routine at a time of day.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.update_available

- sources: ccostan, renemarc
- license: apache_2_0
- chosen: Reporting that a software update is available.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## cleaning.vacuum_schedule

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Running the robot vacuum on a schedule, excluding rooms to avoid.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.alert_flash

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Flashing the house's lights to draw attention to an alert.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.away_shutdown

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Turning the house's lights off when nobody is home.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.brightness_level

- sources: johnkoht, renemarc
- license: no_licence
- chosen: Setting a light's brightness from a control or a schedule.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.motion_light_off

- sources: johnkoht, renemarc
- license: no_licence
- chosen: A room's lights switching themselves off once the motion they answered has stopped and a quiet period has passed.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.occupancy_light_off

- sources: johnkoht, renemarc
- license: no_licence
- chosen: A room's lights following the room leaving its occupied state.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.room_light_dim

- sources: johnkoht, renemarc
- license: no_licence
- chosen: Dropping a room's lights to a low, warm setting for night use.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## lighting.scene_select

- sources: fwartner, renemarc
- license: no_licence
- chosen: Choosing a named lighting scene for a room from a selector control.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## media.room_music

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Starting and stopping music in a room from its own selector.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## media.volume_control

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Managing media volume, including ducking for announcements.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## modes.house_away

- sources: ccostan, johnkoht
- license: no_licence
- chosen: The house's away state, and the shutdown it triggers.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## modes.house_occupancy_summary

- sources: ccostan, johnkoht
- license: no_licence
- chosen: A house-level flag reporting whether anybody is home.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## modes.house_quiet

- sources: johnkoht, renemarc
- license: no_licence
- chosen: A quiet setting that suppresses announcements and dims the house.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## modes.house_vacation

- sources: ccostan, johnkoht
- license: no_licence
- chosen: A longer absence where the house simulates being lived in.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## notifications.engine

- sources: ccostan, johnkoht
- license: no_licence
- chosen: One engine that decides which devices receive a notification.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## notifications.reminder

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Raising a reminder for an event that repeats on a schedule.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## notifications.speech_announce

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Speaking a message through the house's speakers.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## presence.guest_mode

- sources: ccostan, johnkoht
- license: no_licence
- chosen: A guest flag that softens presence-driven automations while visitors stay.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## presence.person_tracking

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Tracking whether a named household member is home.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## presence.vehicle_arrival

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Reacting to a household vehicle arriving home.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## security.alarm_triggered

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Reacting to the alarm sounding.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## security.door_left_open

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Warning that a door or garage door has stayed open.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## security.water_leak_alert

- sources: johnkoht, renemarc
- license: no_licence
- chosen: Reacting to a water leak sensor.
- rejected: Repo-local spellings in renemarc were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.

## system.dashboard_template

- sources: ccostan, johnkoht
- license: no_licence
- chosen: Templating a dashboard section from house state.
- rejected: Repo-local spellings in johnkoht were not adopted where they differed in slot binding, scope or naming; the merged shape above decides.
