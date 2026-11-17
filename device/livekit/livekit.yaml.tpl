port: 7880
bind_addresses:
  - ""
rtc:
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
