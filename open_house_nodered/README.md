# Open House Node-RED

The Node-RED that Open House can answer an input with, shipped as an add-on so a
person who has never installed Node-RED can still use the flow cast. It runs
beside Home Assistant, keeps its own flows, and is reached through Home
Assistant's own ingress -- never a published port.

## If you already run Node-RED

**Keep it. Do nothing.** This add-on is opt-in and the Open House integration
prefers your Node-RED over its own at every point, so there is nothing to turn
off and nothing that will fight what you have.

Concretely, this is how the two of you stay out of each other's way:

- **Your Node-RED is reached only through the address you set.** Open House's
  integration reads a Node-RED address from its own settings
  (`Settings -> Devices & Services -> Open House -> Configure`) and, whenever one
  is set, uses it and *only* it -- to push a flow and to embed the editor. It
  never scans for another Node-RED and never reaches for the community add-on.
  So the community add-on, or a Node-RED of your own, keeps working exactly as
  it does today: you point Open House at it and nothing else changes.
- **This add-on takes no host port.** It is reached through
  [Home Assistant ingress](https://developers.home-assistant.io/docs/apps/presentation)
  (`ingress: true`, `ingress_port: 1880`) and publishes nothing. If you somehow
  have both running, your Node-RED still holds the host's `1880`; this add-on
  never asks for it, so there is no collision to resolve.
- **This add-on has its own data.** Everything it writes lives in *this* add-on's
  `/data`, which the Supervisor keeps separate from the community add-on's
  `/data`. Neither can read or overwrite the other's flows.
- **Open House never starts this add-on.** When you set no Node-RED address, the
  integration asks the Supervisor *about* this one add-on by name -- a read, and
  only when no address is set -- and uses it only if you have installed and
  started it. A stopped add-on, or one you never installed, is invisible to it.
  Nothing in Open House can install, start, or stop anything.

If you would rather keep two separate Node-REDs (one you already have, and this
one) the two never touch: each has its own flows, its own port, and its own Home
Assistant connection. Point Open House's settings at whichever one you want it
to cast through.

## Using it

1. Install this add-on and start it. Give it a name in the sidebar if you like
   (`Watchdog` and `Start on boot` are yours to set).
2. Leave the Open House integration's Node-RED settings **empty**. Open House
   picks this add-on up on its own: the editor embeds the bundled Node-RED, and a
   flow it pushes goes to the bundled Node-RED.
3. The first time you open the editor, the Home Assistant server node is already
   there (`run.sh` seeds it once), connected to your Home Assistant. Build your
   flow and deploy.

If instead you set an address in the Open House settings, that address wins and
this add-on is not used by Open House at all -- which is the right thing for a
house with a Node-RED it already trusts.

## Options

| Option | Description |
| --- | --- |
| `timezone` | Leaves the container's own clock when empty. Given, it is exported as `TZ`, so Node-RED's timestamps are in your local time. |

## What is in the image

`node-red-contrib-home-assistant-websocket` is installed into the image, not
left to the palette manager, so the Home Assistant nodes exist on a fresh
install with nothing to click. The image is built from Node-RED's own official
image; `build.yaml` picks the right one per architecture.

## Development

The add-on is a sibling of the development stack's Node-RED
(`docker/node-red/`), not a replacement for it. The dev stack keeps using its own
container with a published `1880`, because the panel links to that editor from a
browser directly. This add-on is what a real Home Assistant OS install uses,
where ingress replaces the published port.
