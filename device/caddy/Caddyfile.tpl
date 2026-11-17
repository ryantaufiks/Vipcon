{$DEVICE_ID}.local.{$ROOT_DOMAIN} {
	tls {
		dns cloudflare {$CF_DNS_TOKEN}
	}

	handle /rtc* {
		reverse_proxy 127.0.0.1:7880
	}

	handle /api/* {
		reverse_proxy 127.0.0.1:8000
	}

	handle {
		reverse_proxy 127.0.0.1:8000
	}
}
