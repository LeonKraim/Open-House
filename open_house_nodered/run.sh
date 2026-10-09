#!/usr/bin/env bash
# Open House Node-RED -- what the add-on runs.
#
# Three things happen here and the third is the only interesting one: set the
# clock, seed one node on a Node-RED that has never run, and start Node-RED.
set -e

DATA="/data"
OPTIONS="${DATA}/options.json"

# -- The clock, from the add-on's options ------------------------------------
#
# The one setting this add-on has, and it is only ever the clock. Absent or
# empty means the container keeps whatever time zone it was given, which on a
# Home Assistant OS image is already the person's -- so the empty default costs
# nothing and a value only ever makes Node-RED's own timestamps local.
if [ -f "${OPTIONS}" ]; then
    TIMEZONE="$(jq -r '.timezone // empty' "${OPTIONS}" 2>/dev/null || true)"
    if [ -n "${TIMEZONE}" ]; then
        export TZ="${TIMEZONE}"
    fi
fi

# -- The Home Assistant server node, seeded once -----------------------------
#
# **Why this exists at all.** Open House will not invent a server node: a server
# node carries where Home Assistant is and a token, and minting a token and
# writing it into another program's config is not the integration's business. So
# a Node-RED with no server node is refused when a flow is pushed -- a sentence
# telling the person to go and add one. For a Node-RED somebody configured by
# hand that sentence is right, because they already have one and it is better
# than any this could write. For the Node-RED *this add-on ships* there is
# nothing to reuse, so one is seeded here instead, once.
#
# **`addon: true` is the whole trick**, and it is why nothing is written down.
# It is `node-red-contrib-home-assistant-websocket`'s own switch for "I am a
# Home Assistant add-on": the node then connects to `http://supervisor/core`
# with the token the Supervisor has already placed in this add-on's environment
# (`SUPERVISOR_TOKEN`), so no address and no token end up in a flow file. The
# id is `open_house_server`, the fixed id the integration prefers by name when
# it looks for a server node, so a pushed flow references this one.
#
# **Only on a Node-RED that has never run.** The guard is `/data/flows.json`
# being absent -- a fresh install -- so this can never overwrite the flows a
# person has since built, and the file it writes is a plain Node-RED flow array
# that the editor loads and the runtime fills defaults into. `/data` is this
# add-on's own: it is a different directory from the community add-on's, so
# seeding here touches nothing of a Node-RED somebody else runs.
if [ ! -f "${DATA}/flows.json" ]; then
    printf '%s\n' \
        '[{"id":"open_house_server","type":"server","name":"Home Assistant","addon":true}]' \
        > "${DATA}/flows.json"
fi

# -- Node-RED -----------------------------------------------------------------
#
# `npm start` is the official image's own command, run from its own directory,
# with `/data` as the user directory -- so the flows, the credentials and the
# palette's own state live in the add-on's `/data` and nowhere else, exactly as
# the community add-on keeps its own in its own.
cd /usr/src/node-red
exec npm start -- --userDir "${DATA}"
