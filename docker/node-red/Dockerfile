# Open House — a Node-RED beside the test house, for the cast that builds a flow
# instead of typing an expression.
#
# The official image carries no Home Assistant nodes at all, and the flow Open
# House pushes is made of them: an `events: state` trigger to watch the input's
# entity and an `api-call-service` write into the helper Open House created. So
# the package is installed here rather than left to a person through the palette
# manager -- a first-run step is a step a test cannot reproduce, and an image is
# what makes "the flow was pushed and it runs" a thing this repository can
# prove.
FROM nodered/node-red:latest

RUN npm install --unsafe-perm --no-update-notifier --no-fund --omit=dev \
      node-red-contrib-home-assistant-websocket
