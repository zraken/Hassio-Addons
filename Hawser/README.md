[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Apps-Blue?logo=homeassistant&logoColor=%23fff&color=%2303a9f4)](https://www.home-assistant.io/)
![GitHub Release](https://img.shields.io/github/v/release/Finsys/hawser?display_name=tag&label=Hawser%20Release)
![Supports aarch64 Architecture](https://img.shields.io/badge/aarch64-yes-green.svg)
![Supports amd64 Architecture](https://img.shields.io/badge/amd64-yes-green.svg)
![Supports armv7 Architecture](https://img.shields.io/badge/armv7-yes-green.svg)
![Supports armhf Architecture](https://img.shields.io/badge/armhf-no-red.svg)
![Supports i386 Architecture](https://img.shields.io/badge/i386-no-red.svg)

<p align="center">
  <img width="50%" src="https://github.com/Finsys/hawser/raw/main/logo/hawser.png">
</p>

[Hawser](https://github.com/Finsys/hawser) is a lightweight Go agent that enables [Dockhand](https://dockhand.pro/) to manage Docker hosts in various network configurations.

This Home Assistant App (Formerly Add-on) deploys Hawser to your Home Assistant OS or Supervised installations, allowing you to manage and monitor containers. 

> [!CAUTION]
> For Hawser to be able to monitor and manage your Home Assistant Supervisor containers, it needs access to the Docker socket of the host environment. This gives Hawser full access to start, stop or modify any container running on the host, including Home Assistant itself.  

## Installation
1. Please first follow the instructions to [install the repository into your Home Assistant App store](/README.md#installation)
2. Search for "Hawser" in the Home Assistant App store or alternatively click the below button:

    [![Open your Home Assistant instance and show the dashboard of an app.](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=d1f42497_hawser&repository_url=https%3A%2F%2Fgithub.com%2Fzraken%2FHassio-Addons)

3. Install the App 
4. Disable ```Protection mode``` for the App. This is required to allow Hawser to monitor and manage container. 

> [!NOTE]
> **DON'T** start the App before following the below configuration steps.

## Configuration

### Token
Before starting the App, you need to set a security token in App's the configuration. You should set it to a long random string. I'd recommend using a random key generator such as [this one](https://www.strongdm.com/tools/api-key-generator). Use at least a 256-bit long key for good security.

### Edge Mode (OPTIONAL)
By default, Hawser runs in **Standard Mode** where it listens for incoming connections from Dockhand. This is ideal for LAN/homelab setups with static IPs.

Enable **Edge Mode** if your Home Assistant instance is behind NAT or has a dynamic IP. In Edge Mode, Hawser initiates an outbound WebSocket connection to your Dockhand server instead of listening for incoming connections.

To enable Edge Mode:
1. Toggle **Edge mode** on in the App configuration
2. Set the **Dockhand server URL** to your Dockhand instance's WebSocket endpoint (e.g. `wss://your-dockhand.example.com/api/hawser/connect`)

### TLS / HTTPS (OPTIONAL)
Enable **TLS** to secure connections to Hawser using HTTPS.

To enable TLS:
1. Toggle **Enable TLS** on in the App configuration
2. Ensure your certificate files are placed in the configured **Cert directory** (default: `/ssl`)
   - The addon expects `fullchain.pem` and `privkey.pem` in that directory
3. If your certificates are stored in a different directory, update the **Cert directory** path accordingly

> [!NOTE]
> Home Assistant typically stores SSL certificates in `/ssl`. If you've generated certificates through an addon like Let's Encrypt or DuckDNS, they should already be there.


## Start the App
Now start the App and check the logs to see if Hawser has successfully started.

- **Standard Mode**: Add your Home Assistant instance to Dockhand using the IP address of your Home Assistant instance and the port (default `2376`).
- **Edge Mode**: The agent will automatically connect to your Dockhand server. Verify the connection status in the Dockhand dashboard.  