# AWS Volume Troubleshooting Guide for OPAL

This guide covers common AWS volume issues that can affect OPAL deployments and provides comprehensive solutions.

## 🔍 Common AWS Volume Issues

### 1. **Data Persistence Problems**
- **Issue**: Data lost when instance stops/restarts
- **Cause**: Using instance storage instead of EBS volumes
- **Impact**: MongoDB/Rock data disappears on instance restart

### 2. **Performance Issues**
- **Issue**: Slow database operations, high I/O wait
- **Cause**: Incorrect EBS volume type or size
- **Impact**: Poor OPAL performance, timeouts

### 3. **SELinux + Volume Context Issues**
- **Issue**: Containers cannot access mounted volumes
- **Cause**: Incorrect SELinux contexts on EBS volumes
- **Impact**: Container startup failures, permission errors

### 4. **Network + Volume Combination Problems**
- **Issue**: Forward drops preventing volume access
- **Cause**: AWS security groups + container networking
- **Impact**: Connectivity issues between containers and storage

## 🛠️ Diagnostic Commands

Run the comprehensive diagnostic:
```bash
./easy-opal diagnose
```

For AWS-specific checks:
```bash
./easy-opal diagnose --json | jq '.issues[] | select(.category == "aws_volumes")'
```

## 📊 AWS Volume Best Practices

### **1. EBS Volume Configuration**

#### **Choose the Right Volume Type**
```bash
# Check current volume type
aws ec2 describe-volumes --volume-ids vol-xxxxxx

# Recommended types:
# - gp3: General purpose (best price/performance)
# - io1/io2: High-performance databases
# - st1: Throughput-intensive workloads
```

#### **Optimal Sizing**
- **Minimum 100GB for gp3** to get full 3,000 IOPS
- **Size affects performance** - larger volumes get more IOPS
- **Monitor usage** with CloudWatch metrics

### **2. Instance Configuration**

#### **Instance Type Recommendations**
```bash
# Good for OPAL:
# - m5.large or higher (general purpose)
# - c5.large or higher (compute optimized)
# - r5.large or higher (memory optimized for large datasets)

# Avoid:
# - t2/t3 instances for production (burstable performance)
# - Instances without EBS optimization
```

#### **EBS Optimization**
```bash
# Enable EBS optimization
aws ec2 modify-instance-attribute \
  --instance-id i-xxxxxx \
  --ebs-optimized
```

### **3. Volume Mounting**

#### **Proper Mount Configuration**
```bash
# 1. Format the volume (first time only)
sudo mkfs.ext4 /dev/nvme1n1

# 2. Create mount point
sudo mkdir -p /opt/opal-data

# 3. Mount the volume
sudo mount /dev/nvme1n1 /opt/opal-data

# 4. Update fstab for persistence
echo '/dev/nvme1n1 /opt/opal-data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab

# 5. Set proper permissions
sudo chown -R 1000:1000 /opt/opal-data
sudo chmod -R 755 /opt/opal-data
```

#### **Docker Volume Configuration**
Update your docker-compose.yml:
```yaml
services:
  mongo:
    volumes:
      - /opt/opal-data/mongo:/data/db
  
  opal:
    volumes:
      - /opt/opal-data/opal:/srv
      
  rock:
    volumes:
      - /opt/opal-data/rock:/srv
```

### **4. SELinux Configuration for AWS**

#### **SELinux + EBS Volume Setup**
```bash
# 1. Set correct SELinux context for mount point
sudo semanage fcontext -a -t container_file_t "/opt/opal-data(/.*)?"
sudo restorecon -Rv /opt/opal-data

# 2. Enable Docker SELinux booleans
sudo setsebool -P container_manage_cgroup on
sudo setsebool -P container_connect_any on
sudo setsebool -P container_use_cephfs on

# 3. Allow Docker to use the volume
sudo setsebool -P container_use_cephfs on
```

#### **Verify SELinux Context**
```bash
# Check mount point context
ls -Z /opt/opal-data

# Should show: container_file_t or svirt_sandbox_file_t
```

### **5. Performance Optimization**

#### **I/O Queue Depth**
```bash
# Check current queue depth
cat /sys/block/nvme1n1/queue/nr_requests

# Increase for better performance
echo 32 | sudo tee /sys/block/nvme1n1/queue/nr_requests

# Make persistent
echo 'echo 32 > /sys/block/nvme1n1/queue/nr_requests' | sudo tee -a /etc/rc.local
```

#### **Monitor Performance**
```bash
# Check I/O stats
iostat -x 1

# Monitor volume metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/EBS \
  --metric-name VolumeReadOps \
  --dimensions Name=VolumeId,Value=vol-xxxxxx \
  --start-time 2023-01-01T00:00:00Z \
  --end-time 2023-01-01T01:00:00Z \
  --period 300 \
  --statistics Average
```

## 🔧 Troubleshooting Steps

### **Issue 1: Data Lost on Instance Restart**

**Symptoms:**
- OPAL asks for initial setup after restart
- MongoDB/Rock data is gone
- Empty data directories

**Diagnosis:**
```bash
# Check if volumes are properly mounted
df -h
mount | grep opal

# Check if data directories exist
ls -la /opt/opal-data/
```

**Solution:**
```bash
# 1. Stop OPAL
./easy-opal down

# 2. Ensure EBS volume is attached and mounted
lsblk
sudo mount /dev/nvme1n1 /opt/opal-data

# 3. Update docker-compose.yml to use proper volume paths
# 4. Start OPAL
./easy-opal up
```

### **Issue 2: High I/O Wait / Slow Performance**

**Symptoms:**
- OPAL web interface is slow
- Database operations timeout
- High I/O wait in `top`

**Diagnosis:**
```bash
# Check I/O wait
iostat -x 1

# Check volume performance
aws cloudwatch get-metric-statistics \
  --namespace AWS/EBS \
  --metric-name VolumeQueueLength \
  --dimensions Name=VolumeId,Value=vol-xxxxxx \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average
```

**Solution:**
```bash
# 1. Upgrade to gp3 or io1 volume type
aws ec2 modify-volume \
  --volume-id vol-xxxxxx \
  --volume-type gp3 \
  --iops 3000

# 2. Increase volume size if needed
aws ec2 modify-volume \
  --volume-id vol-xxxxxx \
  --size 200

# 3. Optimize queue depth
echo 32 | sudo tee /sys/block/nvme1n1/queue/nr_requests
```

### **Issue 3: SELinux Blocking Volume Access**

**Symptoms:**
- Containers fail to start
- Permission denied errors in logs
- SELinux denials in audit log

**Diagnosis:**
```bash
# Check SELinux denials
sudo ausearch -m avc -ts recent

# Check current context
ls -Z /opt/opal-data/

# Check SELinux status
getenforce
```

**Solution:**
```bash
# 1. Fix SELinux context
sudo semanage fcontext -a -t container_file_t "/opt/opal-data(/.*)?"
sudo restorecon -Rv /opt/opal-data

# 2. Enable necessary booleans
sudo setsebool -P container_manage_cgroup on
sudo setsebool -P container_connect_any on

# 3. Restart Docker
sudo systemctl restart docker

# 4. Restart OPAL
./easy-opal restart
```

### **Issue 4: Network + Volume Combination Problems**

**Symptoms:**
- Containers can't access volumes over network
- Forward drops in network traffic
- Mixed connectivity issues

**Diagnosis:**
```bash
# Check network and volume together
./easy-opal diagnose

# Check security groups
aws ec2 describe-security-groups --group-ids sg-xxxxxx

# Check NACLs
aws ec2 describe-network-acls --network-acl-ids acl-xxxxxx
```

**Solution:**
```bash
# 1. Ensure security groups allow internal traffic
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxx \
  --protocol tcp \
  --port 0-65535 \
  --source-group sg-xxxxxx

# 2. Check NACL rules
aws ec2 describe-network-acls --filters "Name=association.subnet-id,Values=subnet-xxxxxx"

# 3. Verify volume permissions
sudo chown -R 1000:1000 /opt/opal-data
sudo chmod -R 755 /opt/opal-data
```

## 🚀 AWS Console Configuration

### **1. EBS Volume Management**

**To modify volume type:**
1. Go to **EC2 Console** → **Volumes**
2. Select your volume → **Actions** → **Modify Volume**
3. Change type to **gp3** for better performance
4. Adjust size and IOPS as needed

**To create snapshots:**
1. Go to **EC2 Console** → **Volumes**
2. Select volume → **Actions** → **Create Snapshot**
3. Add descriptive name and tags
4. Consider **Lifecycle Manager** for automated snapshots

### **2. Security Group Configuration**

**To allow internal traffic:**
1. Go to **EC2 Console** → **Security Groups**
2. Select your security group → **Inbound Rules**
3. Add rule: **All Traffic** from **Source: This Security Group**
4. This allows containers to communicate

### **3. CloudWatch Monitoring**

**Key metrics to monitor:**
- **VolumeReadOps/VolumeWriteOps**: IOPS usage
- **VolumeQueueLength**: I/O queue depth
- **VolumeTotalReadTime/VolumeTotalWriteTime**: Latency
- **VolumeIdleTime**: Utilization

**To set up alarms:**
1. Go to **CloudWatch Console** → **Alarms**
2. Create alarm for **VolumeQueueLength > 10**
3. Set notification to SNS topic
4. Configure auto-scaling if needed

## 📋 Checklist for AWS OPAL Deployment

### **Pre-Deployment**
- [ ] Choose appropriate instance type (m5.large+ recommended)
- [ ] Attach EBS volumes (gp3, minimum 100GB)
- [ ] Configure security groups for internal traffic
- [ ] Set up backup/snapshot strategy

### **During Deployment**
- [ ] Mount EBS volumes correctly
- [ ] Set proper file permissions (1000:1000)
- [ ] Configure SELinux contexts if enabled
- [ ] Update docker-compose.yml for volume paths

### **Post-Deployment**
- [ ] Run diagnostic: `./easy-opal diagnose`
- [ ] Verify data persistence with restart test
- [ ] Set up CloudWatch monitoring
- [ ] Configure automated backups

### **Ongoing Maintenance**
- [ ] Monitor volume performance metrics
- [ ] Regular snapshot creation
- [ ] Update volume type/size as needed
- [ ] Monitor SELinux audit logs

## 🆘 Emergency Recovery

### **Data Recovery from Snapshot**
```bash
# 1. Create volume from snapshot
aws ec2 create-volume \
  --size 100 \
  --volume-type gp3 \
  --snapshot-id snap-xxxxxx \
  --availability-zone us-east-1a

# 2. Attach to instance
aws ec2 attach-volume \
  --volume-id vol-xxxxxx \
  --instance-id i-xxxxxx \
  --device /dev/sdf

# 3. Mount and restore
sudo mkdir -p /mnt/recovery
sudo mount /dev/nvme2n1 /mnt/recovery
sudo cp -r /mnt/recovery/* /opt/opal-data/
```

### **Instance Migration**
```bash
# 1. Stop OPAL
./easy-opal down

# 2. Create snapshot of current volume
aws ec2 create-snapshot --volume-id vol-xxxxxx

# 3. Detach volume
aws ec2 detach-volume --volume-id vol-xxxxxx

# 4. Attach to new instance
aws ec2 attach-volume \
  --volume-id vol-xxxxxx \
  --instance-id i-new-instance \
  --device /dev/sdf

# 5. Mount and start
sudo mount /dev/nvme1n1 /opt/opal-data
./easy-opal up
```

## 📚 Additional Resources

- [AWS EBS Documentation](https://docs.aws.amazon.com/ebs/)
- [Docker Volume Management](https://docs.docker.com/storage/volumes/)
- [SELinux and Containers](https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/7/html/selinux_users_and_administrators_guide/sect-security-enhanced_linux-working_with_selinux-selinux_contexts_labeling_files)
- [AWS CloudWatch Monitoring](https://docs.aws.amazon.com/cloudwatch/)

---

## 🔄 Automated Diagnostic

For comprehensive troubleshooting, always start with:
```bash
./easy-opal diagnose
```

This will check all volume, network, and security configurations automatically and provide specific guidance for your AWS environment. 