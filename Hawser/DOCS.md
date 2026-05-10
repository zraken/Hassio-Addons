# Hawser

Docker stack deployment and management service, giving Dockhand access to your Home Assistant environment.

## Security Note
For Hawser to be able to monitor and manage your Home Assistant Supervisor containers, it needs access to the Docker socket of the host environment. This give Hawser full access to start, stop or modify any container running on the host, including Home Assistant itself.

## Protection mode
Protection mode must be **disabled** for this App in order for it to have access to the host docker environment.

## Configuration

| Option  | Required | Description |
|---------|----------|-------------|
| `token` | Yes      | Secret token used to authenticate API requests from Dockhand. Must be changed from default value. |
| `edge_mode` | No | Enable Edge Mode. The agent initiates an outbound WebSocket connection to Dockhand instead of listening for incoming connections. Ideal for NAT or dynamic IP setups. Default: `false` |
| `dockhand_server_url` | No | WebSocket URL of your Dockhand server for Edge Mode (e.g. `wss://your-dockhand.example.com/api/hawser/connect`). Required when Edge Mode is enabled. |
| `enable_tls` | No | Enable TLS/HTTPS for secure connections. Expects `fullchain.pem` and `privkey.pem` in the configured cert directory. Default: `false` |
| `certdir` | No | Directory containing TLS certificate files. Default: `/ssl` |

## Ports

| Option  | Required | Description |
|---------|----------|-------------|
| `port`  | Yes      | Hawser Docker management API port. Defaults to `2376` |