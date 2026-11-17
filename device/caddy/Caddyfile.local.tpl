(vipcon_routes) {
	handle /rtc* {
		reverse_proxy 127.0.0.1:7880
	}

	handle /api/* {
		reverse_proxy 127.0.0.1:8000
	}

	handle {
		root * /opt/appliance/web
		try_files {path} /index.html
		file_server
	}
}

# Fallback names, served with the device's own local CA.
{$DEVICE_ID}.local.{$ROOT_DOMAIN}, {$HOSTNAME}.lan, {$HOSTNAME}, {$LAN_IP} {
	tls /etc/appliance/ca/server.pem /etc/appliance/ca/server-key.pem
	import vipcon_routes
}

:8080 {
	import vipcon_routes
}

# The office-network name (https://<site>.lan.<root>) with its Let's Encrypt certificate,
# written by the agent once the control plane has issued it. Absent until then.
import /etc/appliance/caddy-site*.caddy
