(vipcon_routes) {
	# Only the tunnel listener below may say a request came from the internet.
	request_header -X-Vipcon-Public

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

# Internet access: requests arriving through the device's Cloudflare tunnel (cloudflared connects
# to this localhost-only listener). Only meeting pages and the guest APIs are reachable; admin,
# claim and account APIs stay on the office network.
http://:8081 {
	bind 127.0.0.1
	request_header X-Vipcon-Public 1

	handle /rtc* {
		reverse_proxy 127.0.0.1:7880
	}

	@guest_api path /api/v1/join /api/v1/lobby/status /api/v1/meetings/now /api/v1/meeting-info
	handle @guest_api {
		reverse_proxy 127.0.0.1:8000
	}

	@lan_only path / /api/* /admin /admin/* /claim /claim/* /healthz
	handle @lan_only {
		respond "Not found" 404
	}

	handle {
		root * /opt/appliance/web
		try_files {path} /index.html
		file_server
	}
}

# The office-network name (https://<site>.lan.<root>) with its Let's Encrypt certificate,
# written by the agent once the control plane has issued it. Absent until then.
import /etc/appliance/caddy-site*.caddy
