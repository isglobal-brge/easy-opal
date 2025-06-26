# SSL Certificate Configuration Guide

This guide provides a detailed explanation of the different SSL strategies available in `easy-opal`. Choosing the right strategy is crucial for securing your Opal stack.

---

## 1. `self-signed` (Default & Recommended for Development)

This is the simplest method for local development and testing. It uses `mkcert` to generate SSL certificates that are **automatically trusted by your local machine's web browsers**. This means you get the green padlock in your address bar without any manual steps or security warnings.

### How It Works

1.  **Local Certificate Authority (CA):** The `./setup` script ensures `mkcert` is installed and that its local CA is registered with your system's trust store. This is a one-time operation.
2.  **Certificate Generation:** When you run `./easy-opal setup` with this strategy, the tool:
    -   Automatically detects your local hostnames and IP addresses (e.g., `localhost`, `127.0.0.1`, `192.168.1.XX`).
    -   Allows you to add any other hostnames you might use for testing (e.g., `my-local-opal.dev`).
    -   Uses `mkcert` to generate a `cert.crt` and `key.key` file valid for all those names.
    -   Places the generated files in the `./data/nginx/certs/` directory, ready to be used by NGINX.

### When to Use It

-   **Always** for local development on `localhost`.
-   Testing on a private network where you can access the server via its IP address.

---

## 2. `letsencrypt` (Recommended for Production)

This strategy uses [Let's Encrypt](https://letsencrypt.org/) to provision a free, publicly trusted SSL certificate. This is the standard choice for any production or publicly accessible server.

### Prerequisites

For this method to succeed, you **must** have the following set up *before* running the command:

1.  **A registered domain name** (e.g., `my-opal.my-domain.com`).
2.  **A public IP address** for your server.
3.  **A DNS 'A' record** pointing your domain name to your server's public IP address.
4.  **Ports 80 and 443 open** on your server's firewall and security groups.

### The Validation Flow (The "Chicken-and-Egg" Solution)

The Let's Encrypt process needs to verify that you control the domain name before it issues a certificate. It does this using a process called the `HTTP-01` challenge, which our tool automates perfectly. Here's how it avoids a deadlock:

1.  **A Temporary Server:** The `setup` command starts a minimal, temporary NGINX instance that listens **only on port 80 (HTTP)**. It does not yet have an SSL certificate.
2.  **The Challenge Request:** `certbot` (the Let's Encrypt client) contacts the Let's Encrypt servers. The servers challenge `certbot` to prove domain ownership by asking it to place a specific file at a specific URL.
3.  **Serving the Challenge:** `certbot` places the challenge file in a shared directory (`./data/letsencrypt/www`) that the temporary NGINX server can access.
4.  **Verification:** The Let's Encrypt servers then make a plain HTTP request to `http://<your-domain>/.well-known/acme-challenge/...`. Our temporary NGINX server serves the file, proving ownership.
5.  **Certificate Issued:** With verification complete, Let's Encrypt issues the SSL certificate. `certbot` saves it to `./data/letsencrypt/conf`.
6.  **Cleanup:** The temporary NGINX server is stopped. The paths to the new certificate and key are saved to your `config.json`.

The next time you run `./easy-opal up`, the main NGINX service will start, configured for **port 443 (HTTPS)**, and it will use the newly acquired certificate.

---

## 3. `manual`

This strategy is for advanced users who want to use a certificate from a different Certificate Authority (e.g., a commercial SSL provider or an internal corporate CA).

### How It Works

1.  During the `setup` wizard, you will be prompted to provide the **full, absolute path** to your existing certificate file (`.crt` or `.pem`) and your private key file (`.key`).
2.  `easy-opal` will then copy these files into the `./data/nginx/certs/` directory.
3.  The NGINX container will be configured to use these copied certificates.

> **Important:** With this method, you are responsible for managing the certificate's lifecycle, including renewals. The tool will not handle it for you.

---

## 4. `reverse-proxy` (Advanced)

This strategy is for advanced users who are deploying `easy-opal` behind an existing reverse proxy (e.g., another NGINX instance, Traefik, Caddy, or a cloud load balancer like an AWS ALB).

In this mode, the external proxy is responsible for **SSL termination** (handling all public HTTPS traffic). The `easy-opal` stack communicates with your proxy over plain, unencrypted HTTP.

### How It Works

1.  **No SSL:** The `easy-opal` NGINX container does not handle any SSL certificates or listen on port 443.
2.  **HTTP Only:** It listens on a plain HTTP port (default `80`) inside the Docker network.
3.  **Exposed Port:** During setup, you specify a local port on the host machine (e.g., `8080`) that will be mapped to the NGINX container's internal port 80.
4.  **External Proxy Configuration:** You must configure your external reverse proxy to forward traffic to the `easy-opal` stack on the exposed HTTP port (e.g., forward `https://my-opal.domain.com` to `http://<easy-opal-host-ip>:8080`).

### When to Use It

-   When integrating into an existing infrastructure that already has a centralized reverse proxy or load balancer.
-   In corporate environments where SSL is handled at the network edge. 