# Troubleshooting Guide

## Docker Detection Issues

### Problem: "Docker is not installed or not running" despite being installed

**Symptoms:**
- The setup fails with "Docker is not installed or not running"
- Docker is actually installed and working manually

**Cause:**
This usually happens because different Linux distributions ship with different versions of Docker Compose:
- **Debian**: Often ships with Docker Compose V1 (`docker-compose` with hyphen)
- **Ubuntu**: Usually has Docker Compose V2 (`docker compose` with space)

**Solution:**
The latest version of easy-opal automatically detects both versions. If you're still having issues:

1. **Check your Docker installation:**
   ```bash
   docker --version
   docker ps
   ```

2. **Check Docker Compose version:**
   ```bash
   # Try V2 first
   docker compose version
   # If that fails, try V1
   docker-compose --version
   ```

3. **Install Docker Compose V2 (recommended):**
   ```bash
   # On Debian/Ubuntu
   sudo apt-get update
   sudo apt-get install docker-compose-plugin
   ```

### Docker Version Compatibility

**Supported Docker Versions:**
- ✅ **Docker Engine 17.06+** (recommended)
- ⚠️  **Docker Engine 1.13-17.05** (basic support, some features limited)
- ❌ **Docker Engine < 1.13** (not supported)

**Check your Docker version:**
```bash
docker --version
docker info
```

**If you have an older Docker version:**
- **Ubuntu/Debian**: `sudo apt-get update && sudo apt-get install docker-ce`
- **CentOS/RHEL**: `sudo yum update docker-ce`
- **Manual upgrade**: Follow [Docker's official installation guide](https://docs.docker.com/engine/install/)

**Version-specific notes:**
- **Docker < 1.13**: `docker pull` syntax only (no `docker image pull`)
- **Docker < 17.06**: Limited network inspection capabilities
- **Docker < 20.10**: Some compose features may not work

---

## Service Connectivity Issues

### Problem: Opal and Rock services can't connect in Debian

**Symptoms:**
- Services start successfully but can't communicate
- Connection timeouts between containers
- Works in Ubuntu but fails in Debian

**Common Causes & Solutions:**

#### 1. AppArmor Conflicts
**Debian's AppArmor profiles can interfere with Docker networking.**

**Check if AppArmor is the issue:**
```bash
sudo dmesg | grep apparmor | grep DENIED
```

**Solutions:**
```bash
# Option 1: Disable AppArmor for Docker (temporary)
sudo aa-disable /etc/apparmor.d/docker

# Option 2: Put Docker profile in complain mode
sudo aa-complain /etc/apparmor.d/docker

# Option 3: Restart AppArmor after Docker starts
sudo systemctl restart apparmor
sudo systemctl restart docker
```

#### 2. Bridge Network Issues
**Debian sometimes has different bridge network defaults.**

**Check bridge network configuration:**
```bash
docker network inspect bridge
docker network ls
```

**Solution - Use the improved network configuration:**
The latest version of easy-opal uses an enhanced bridge network configuration with:
- **Dynamic subnet allocation** - Automatically finds available IP ranges to avoid conflicts
- DNS aliases for better service discovery  
- Bridge-specific settings for improved compatibility

The tool automatically scans existing Docker networks and chooses an available subnet from:
1. `172.16.0.0/16` to `172.31.0.0/16` (preferred)
2. `192.168.100.0/24` to `192.168.254.0/24` (fallback)
3. Docker auto-assignment (last resort)

If you're still having issues, try recreating the network:
```bash
# Remove existing containers
./easy-opal down

# Remove the network (if it exists)
docker network rm $(docker network ls -q --filter name=opal)

# Restart to recreate with new configuration
./easy-opal up
```

**If you get "Pool overlaps with other one" error:**
```bash
# This means there's a subnet conflict. The tool should handle this automatically,
# but if it doesn't, try these steps:

# 1. Clean up orphaned networks
docker network prune

# 2. List networks to see what's conflicting
docker network ls
docker network inspect <network-name>

# 3. Force regenerate with different subnet
./easy-opal down
docker network rm $(docker network ls -q --filter name=opal) 2>/dev/null || true
./easy-opal up
```

**Alternative - Create custom bridge network:**
```bash
# Remove existing containers
./easy-opal down

# Create custom bridge network
docker network create --driver bridge opal-custom-net

# Update your configuration to use custom network
# (This may require modifying the docker-compose template)
```

#### 3. Firewall/iptables Issues
**Debian's firewall rules can block container communication.**

**Check iptables rules:**
```bash
sudo iptables -L DOCKER-USER
sudo iptables -L FORWARD
```

**Solution:**
```bash
# Allow Docker bridge traffic
sudo iptables -I FORWARD -i docker0 -o docker0 -j ACCEPT

# Make persistent (Debian/Ubuntu)
sudo apt-get install iptables-persistent
sudo netfilter-persistent save
```

#### 4. DNS Resolution Issues
**Container DNS might not work properly in Debian.**

**Test DNS in container:**
```bash
docker run --rm -it busybox nslookup google.com
```

**Solution:**
```bash
# Use custom DNS in docker daemon
sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
    "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF

sudo systemctl restart docker
```

#### 5. Network Bridge Module Issues
**The br_netfilter module might not be loaded properly.**

**Check and load required modules:**
```bash
# Check if modules are loaded
lsmod | grep br_netfilter
lsmod | grep overlay

# Load modules if missing
sudo modprobe br_netfilter
sudo modprobe overlay

# Make persistent
echo 'br_netfilter' | sudo tee -a /etc/modules
echo 'overlay' | sudo tee -a /etc/modules
```

---

## Debug Container Connectivity

### Test Container-to-Container Communication

1. **Start your stack:**
   ```bash
   ./easy-opal up
   ```

2. **Check container IPs:**
   ```bash
   # For newer Docker versions (17.06+)
   docker network inspect $(docker-compose ps -q | head -1 | xargs docker inspect --format='{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}') | grep -A 5 "IPv4Address"
   
   # Alternative for older Docker versions
   docker inspect $(docker-compose ps -q) | grep -A 5 "IPAddress"
   ```

3. **Test connectivity from Opal container:**
   ```bash
   # Get into the Opal container
   docker exec -it <stack-name>-opal bash
   
   # Try to reach Rock container
   ping <stack-name>-rock
   curl http://<stack-name>-rock:8085
   ```

4. **Check Rock service status:**
   ```bash
   # Get into Rock container
   docker exec -it <stack-name>-rock bash
   
   # Check if Rock is listening (try both commands for compatibility)
   netstat -tlnp | grep 8085 || ss -tlnp | grep 8085
   ```

### Check Docker Logs

```bash
# Check all container logs
docker-compose logs

# Check specific service logs
docker logs <stack-name>-opal
docker logs <stack-name>-rock

# Follow logs in real-time
docker-compose logs -f
```

---

## Distribution-Specific Notes

### Debian Bullseye/Bookworm
- May require AppArmor configuration
- Often ships with Docker Compose V1
- May need manual bridge network setup

### Ubuntu 20.04/22.04
- Usually works out of the box
- Ships with Docker Compose V2
- Better Docker integration

### Other Debian-based distributions
- Debian-based distributions (like DietPi, Raspberry Pi OS) may have similar issues
- Follow the Debian troubleshooting steps above

---

## Getting Help

If none of these solutions work:

1. **Gather debug information:**
   ```bash
   # System info
   uname -a
   cat /etc/os-release
   
   # Docker info (enhanced for version compatibility)
   docker --version
   docker info
   
   # Docker Compose info (try both versions)
   docker-compose --version || docker compose version
   
   # Network info
   docker network ls
   ip addr show docker0
   
   # Docker version compatibility check
   echo "Checking Docker version compatibility..."
   DOCKER_VERSION=$(docker --version | sed 's/.*version \([0-9]\+\.[0-9]\+\).*/\1/')
   echo "Docker version: $DOCKER_VERSION"
   ```

2. **Create an issue** with the debug information at: [https://github.com/davidsarratgonzalez/easy-opal/issues](https://github.com/davidsarratgonzalez/easy-opal/issues) 