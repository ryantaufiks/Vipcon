{$DEVICE_ID}.local.{$ROOT_DOMAIN}, {$HOSTNAME}.lan, {$HOSTNAME}, {$LAN_IP} {
	tls /etc/appliance/ca/server.pem /etc/appliance/ca/server-key.pem

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

:8080 {
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
