# Open House Node-RED -- the image.
#
# `BUILD_FROM` is Node-RED's own official image, chosen per architecture in
# `build.yaml`, so this file adds to a working Node-RED rather than assembling
# one. That is the same shape as the development stack's `docker/node-red/`,
# which builds the palette into a Node-RED beside the test house for exactly the
# same reason: a first-run palette step is a step nothing can prove.
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Root for the two installs below. The base image runs as its own `node` user,
# which owns nothing outside `/usr/src/node-red`; the add-on itself runs as root
# (the Home Assistant default), which is what makes `/data` -- the Supervisor's,
# and root's -- writable by the entrypoint.
USER root

# The palette Open House's flow is made of. Installed **into Node-RED's own
# directory and not into `/data`**, so it is a fact about the image: it is there
# on a fresh install with nothing to click, it survives a reset of a person's
# flows, and it is never written into a directory that belongs to anyone else.
# Node-RED loads nodes from its install directory's `node_modules`, which is
# where this puts them -- the same place, and the same command, as the dev
# stack's Node-RED image.
RUN cd /usr/src/node-red \
 && npm install --unsafe-perm --no-update-notifier --no-fund --omit=dev \
      node-red-contrib-home-assistant-websocket

# jq, so `run.sh` can read the options the Supervisor writes to
# `/data/options.json`. The base image is Debian without it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends jq \
 && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod a+x /run.sh

# The image's own entrypoint is cleared rather than inherited: it exists to drop
# privileges to `node`, and this add-on runs as root deliberately (see above).
# `/run.sh` is the whole of what starts here.
ENTRYPOINT []
CMD [ "/run.sh" ]
