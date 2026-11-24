port: 7880
# API and signalling on localhost only: browsers reach /rtc through Caddy. Media ports
# (rtc.tcp_port, rtc.udp_port) still listen on the LAN.
bind_addresses:
  - "127.0.0.1"
rtc:
  # Never offer the Docker bridge to browsers (every container uses the host network).
  interfaces:
    excludes:
      - docker0
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false
  node_ip: __LAN_IP__
keys:
  __LIVEKIT_KEY__: __LIVEKIT_SECRET__
logging:
  level: info
room:
  empty_timeout: 300
