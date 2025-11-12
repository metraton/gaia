# Especificación Exhaustiva de Permisos: AWS, GCP, Docker y Kubernetes

**Generado:** 2025-11-12
**Versión:** 2.0
**Propósito:** Definir reglas inteligentes y exhaustivas para permitir/bloquear comandos CLI basadas en documentación oficial
**Alcance:** AWS CLI, GCP gcloud, Docker CLI, Kubernetes kubectl

---

## Tabla de Contenidos

1. [Estrategia de Clasificación](#estrategia-de-clasificación)
2. [AWS CLI - Especificación Completa](#aws-cli---especificación-completa)
3. [GCP gcloud - Especificación Completa](#gcp-gcloud---especificación-completa)
4. [Docker CLI - Especificación Completa](#docker-cli---especificación-completa)
5. [Patrones de Decisión Unificados](#patrones-de-decisión-unificados)
6. [Matriz de Decisión Rápida](#matriz-de-decisión-rápida)
7. [Ejemplos Testables](#ejemplos-testables)

---

## Estrategia de Clasificación

### Categorías de Comandos

Todos los comandos CLI se clasifican según su impacto en **4 dimensiones**:

| Dimensión | T0 (Permitido) | T1 (Permitido) | T2 (Ask) | T3 (Bloqueado) |
|-----------|---|---|---|---|
| **Lectura** | ✅ Describe, List, Get, Show | - | - | - |
| **Consulta** | ✅ Status, Config, Version, Logs | - | - | - |
| **Validación** | ✅ Validate, Lint, Dry-run | ✅ Local changes | - | - |
| **Creación** | - | - | ✅ Create, Apply, Install | - |
| **Modificación** | - | - | ✅ Update, Patch, Upgrade | - |
| **Reversible** | - | - | ✅ Rollback, Suspend, Resume | - |
| **Irreversible** | - | - | ⚠️ Delete, Destroy, Terminate* | ❌ Delete (cluster/project/data) |
| **Destructiva** | - | - | - | ❌ Destroy, Drop, Truncate, -f |

*Nota: Algunos delete (e.g., kubectl delete pod) van en T2 (Ask), mientras que delete críticos (e.g., gcloud container clusters delete) van en T3 (Bloqueado).

### Regla Oro: Matriz de Decisión

```
┌─ ¿Es Read-Only?
│  └─ YES: T0 (PERMITIDO)
│
├─ ¿Es Validación/Dry-Run?
│  └─ YES: T0 o T1 (PERMITIDO)
│
├─ ¿Es Crear/Modificar?
│  ├─ ¿Afecta recursos críticos? (cluster, database, IAM, firewall)
│  │  └─ YES: T3 (BLOQUEADO - demasiado riesgo sin contexto)
│  └─ ¿Recurso es reversible?
│     └─ YES: T2 (ASK - requiere aprobación usuario)
│     └─ NO: T3 (BLOQUEADO - no reversible)
│
└─ ¿Es Eliminar/Destruir?
   ├─ ¿Es data crítica? (cluster, database, project, VPC, firewall)
   │  └─ YES: T3 (BLOQUEADO - demasiado peligroso)
   └─ ¿Es recurso no-crítico? (pod, service, temp image)
      └─ YES: T2 (ASK - requiere aprobación usuario)
```

---

## AWS CLI - Especificación Completa

### T0: Permitido (Read-Only)

**Patrones permitidos:**

```bash
# ✅ S3 - Lectura
aws s3 ls                          # List buckets
aws s3 ls s3://bucket-name         # List objects in bucket
aws s3 cp s3://bucket/file -       # Download object (stdout)
aws s3api head-object             # Get object metadata
aws s3api list-object-versions    # List versions
aws s3api get-object-acl          # Get object ACL (read)

# ✅ EC2 - Descripción
aws ec2 describe-instances                    # List all instances
aws ec2 describe-instances --instance-ids X  # Specific instance
aws ec2 describe-security-groups              # List security groups
aws ec2 describe-volumes                      # List volumes
aws ec2 describe-vpcs                         # List VPCs
aws ec2 describe-subnets                      # List subnets
aws ec2 describe-key-pairs                    # List key pairs
aws ec2 describe-snapshots                    # List snapshots
aws ec2 describe-images                       # List AMIs

# ✅ RDS - Lectura
aws rds describe-db-instances              # List databases
aws rds describe-db-clusters               # List clusters
aws rds describe-db-parameter-groups       # List parameter groups
aws rds describe-db-security-groups        # List security groups (RDS-Classic)

# ✅ IAM - Lectura
aws iam list-users                         # List users
aws iam list-groups                        # List groups
aws iam list-roles                         # List roles
aws iam list-policies                      # List policies
aws iam list-access-keys --user-name X    # List access keys for user
aws iam get-user                           # Get current user info
aws iam get-role --role-name X            # Get role details
aws iam get-policy --policy-arn X         # Get policy details
aws iam list-attached-user-policies        # List attached policies for user

# ✅ Lambda - Lectura
aws lambda list-functions                  # List Lambda functions
aws lambda get-function --function-name X # Get function details
aws lambda get-function-code --function-name X  # Get function code (doesn't execute)

# ✅ CloudWatch - Lectura
aws cloudwatch describe-alarms              # List alarms
aws cloudwatch describe-metric-alarms       # List metric alarms
aws cloudwatch get-metric-statistics        # Get metrics (read)
aws cloudwatch list-metrics                 # List available metrics

# ✅ CloudFormation - Lectura
aws cloudformation describe-stacks              # List stacks
aws cloudformation describe-stack-resources    # List stack resources
aws cloudformation get-template                # Get template (read)
aws cloudformation list-stacks                 # List stacks

# ✅ DynamoDB - Lectura
aws dynamodb list-tables                   # List tables
aws dynamodb describe-table --table-name X # Table details
aws dynamodb get-item --table-name X       # Read item (scan)

# ✅ S3 - Tags y metadatos
aws s3api get-bucket-versioning   # Check versioning status
aws s3api get-bucket-acl          # Get bucket ACL (read)
aws s3api get-bucket-policy       # Get bucket policy (read)
aws s3api get-bucket-tagging      # Get bucket tags

# ✅ Logs y auditoría
aws logs describe-log-groups           # List CloudWatch log groups
aws logs describe-log-streams          # List log streams
aws logs get-log-events                # Read log events
aws s3api list-bucket-metrics-configurations  # List metrics config

# ✅ Configuración y estado
aws sts get-caller-identity            # Get current AWS account/user info
aws ec2 describe-account-attributes    # Get account attributes
aws ec2 describe-regions               # List available regions
aws ec2 describe-availability-zones    # List availability zones
```

**Patrones explícitamente bloqueados en T0:**
- ❌ Ninguno que contenga: `--dryrun` (validación va en T1)
- ❌ Ninguno que contenga: `--output` a fichero sin read (usa T0 para stdout)

---

### T1: Permitido (Validación Local)

**Patrones permitidos:**

```bash
# ✅ Validación con --dryrun
aws ec2 run-instances --dryrun ...              # Preview instance launch
aws ec2 terminate-instances --dryrun ...       # Preview termination
aws ec2 create-security-group --dryrun ...     # Preview SG creation
aws s3 rm s3://bucket/file --dryrun            # Preview S3 deletion

# ✅ Terraform/CloudFormation validación
aws cloudformation validate-template --template-body file://template.json
aws cloudformation get-template-summary        # Validate template

# ✅ Cambios locales reversibles
git add .claudeignore                  # Local staging (preparación)
python3 -m json.tool config.json       # Validate JSON locally
terraform fmt -write=false             # Check Terraform formatting

# ✅ Simulación de cambios
terraform plan -out=plan.tfplan        # Plan without apply
terraform show plan.tfplan             # Show planned changes

# ✅ Tags y metadata (no destructivo)
aws ec2 create-tags --resources i-xxxxx --tags Key=Name,Value=Test  # Local tag (reversible si instance no existe)
```

**Nota:** Los cambios locales que son verdaderamente reversibles (git add, local file edits) se permiten en T1 sin Ask.

---

### T2: Ask (Requiere Aprobación)

**Patrones que requieren Ask:**

```bash
# ⚠️ Crear/Modificar - Instancias no críticas
aws ec2 run-instances --image-id ami-xxxxx --instance-type t2.micro
aws ec2 modify-instance-attribute --instance-id i-xxxxx --attribute xxx

# ⚠️ Crear/Modificar - Security Groups
aws ec2 create-security-group --group-name my-sg --description "..."
aws ec2 authorize-security-group-ingress --group-id sg-xxxxx --protocol tcp --port 80
aws ec2 authorize-security-group-egress --group-id sg-xxxxx --protocol tcp --port 443

# ⚠️ Crear/Modificar - Redes
aws ec2 create-vpc --cidr-block 10.0.0.0/16
aws ec2 create-subnet --vpc-id vpc-xxxxx --cidr-block 10.0.1.0/24
aws ec2 create-network-interface --subnet-id subnet-xxxxx

# ⚠️ Crear/Modificar - Almacenamiento
aws ec2 create-volume --size 100 --availability-zone us-east-1a
aws ec2 attach-volume --volume-id vol-xxxxx --instance-id i-xxxxx --device /dev/sdf
aws rds create-db-instance --db-instance-identifier mydb --master-username admin ...

# ⚠️ Crear/Modificar - IAM (no crítico)
aws iam create-user --user-name newuser
aws iam create-access-key --user-name existing-user
aws iam attach-user-policy --user-name user --policy-arn arn:aws:iam::aws:policy/PowerUser

# ⚠️ Crear/Modificar - Lambda
aws lambda create-function --function-name myfunc --runtime python3.11 --role arn:aws:iam::123456:role/lambda-role
aws lambda update-function-code --function-name myfunc --zip-file fileb://code.zip
aws lambda update-function-configuration --function-name myfunc --timeout 60

# ⚠️ Crear/Modificar - S3 (buckets no críticos)
aws s3 mb s3://my-new-bucket
aws s3 cp file.txt s3://bucket/file.txt
aws s3api put-object --bucket bucketname --key keyname --body file.txt
aws s3api put-bucket-versioning --bucket bucketname --versioning-configuration Status=Enabled
aws s3api put-bucket-policy --bucket bucketname --policy file://policy.json

# ⚠️ Modificar - CloudFormation (no destruir)
aws cloudformation create-stack --stack-name mystack --template-body file://template.json
aws cloudformation update-stack --stack-name mystack --template-body file://updated.json

# ⚠️ Iniciar/Parar instancias (reversible)
aws ec2 start-instances --instance-ids i-xxxxx
aws ec2 stop-instances --instance-ids i-xxxxx
aws ec2 reboot-instances --instance-ids i-xxxxx

# ⚠️ Snapshot/Backup (reversible)
aws ec2 create-snapshot --volume-id vol-xxxxx --description "backup"
aws rds create-db-snapshot --db-instance-identifier mydb --db-snapshot-identifier mydb-snapshot

# ⚠️ Aplicar cambios reversibles
aws rds modify-db-instance --db-instance-identifier mydb --master-user-password newpassword
aws rds reboot-db-instance --db-instance-identifier mydb
```

---

### T3: Bloqueado (Nunca permitido sin aprobación extrema)

**Patrones explícitamente bloqueados:**

```bash
# ❌ NUNCA - Eliminar instancias
❌ aws ec2 terminate-instances --instance-ids i-xxxxx

# ❌ NUNCA - Eliminar bases de datos
❌ aws rds delete-db-instance --db-instance-identifier mydb
❌ aws rds delete-db-cluster --db-cluster-identifier mycluster

# ❌ NUNCA - Eliminar VPC (infraestructura crítica)
❌ aws ec2 delete-vpc --vpc-id vpc-xxxxx
❌ aws ec2 delete-subnet --subnet-id subnet-xxxxx

# ❌ NUNCA - Eliminar storage
❌ aws ec2 delete-volume --volume-id vol-xxxxx
❌ aws s3 rb s3://bucket-name           # Eliminar bucket (data crítica)
❌ aws s3 rm s3://bucket-name --recursive # Borrar todo bucket

# ❌ NUNCA - Modificar IAM crítico (crear/eliminar roles/policies)
❌ aws iam delete-role --role-name role-name
❌ aws iam delete-policy --policy-arn arn:aws:iam::123456:policy/mypolicy
❌ aws iam attach-role-policy --role-name role --policy-arn arn:... (si es role crítico)
❌ aws iam create-role --role-name admin-role --assume-role-policy-document ...

# ❌ NUNCA - Modificar policies críticas
❌ aws iam put-role-policy --role-name role --policy-name policy --policy-document ...
❌ aws iam put-user-policy --user-name user --policy-name policy --policy-document ...

# ❌ NUNCA - Eliminar Lambda
❌ aws lambda delete-function --function-name myfunc

# ❌ NUNCA - Eliminar CloudFormation stacks
❌ aws cloudformation delete-stack --stack-name mystack

# ❌ NUNCA - Eliminar CloudWatch
❌ aws logs delete-log-group --log-group-name /aws/lambda/myfunc

# ❌ NUNCA - Eliminar backups/snapshots
❌ aws ec2 delete-snapshot --snapshot-id snap-xxxxx
❌ aws rds delete-db-snapshot --db-snapshot-identifier mydb-snapshot

# ❌ NUNCA - Cambiar credenciales críticas
❌ aws iam delete-access-key --user-name user --access-key-id AKIAIOSFODNN7EXAMPLE
❌ aws iam update-access-key --user-name user --access-key-id AKIA... --status Inactive (si es credencial activa principal)

# ❌ NUNCA - Operaciones que implican --force o similares
❌ aws ec2 delete-network-interface --network-interface-id eni-xxxxx (si está attached)
❌ aws s3 rm s3://bucket/file --recursive --force

# ❌ NUNCA - Cambiar proyecto/account
❌ aws iam create-account-alias
❌ aws organizations deregister-delegated-administrator
```

---

## GCP gcloud - Especificación Completa

### T0: Permitido (Read-Only)

**Patrones permitidos:**

```bash
# ✅ Configuración
gcloud config list                     # List current configuration
gcloud config get-value project        # Get current project
gcloud config get-value compute/region # Get current region
gcloud auth list                       # List authorized accounts
gcloud version                         # Show gcloud version

# ✅ Compute Engine - Lectura
gcloud compute instances list                          # List VMs
gcloud compute instances list --format=table           # Format output
gcloud compute instances describe instance-name        # Describe VM
gcloud compute instances list --zones=zone-name        # List in zone
gcloud compute operations list                         # List operations
gcloud compute images list                             # List images
gcloud compute snapshots list                          # List snapshots
gcloud compute disk-types list                         # List disk types

# ✅ Kubernetes - Lectura
gcloud container clusters list                         # List GKE clusters
gcloud container clusters describe cluster-name        # Describe cluster
gcloud container clusters get-credentials cluster-name # Get kubeconfig (read)
gcloud container node-pools list --cluster=cluster     # List node pools
gcloud container operations list                       # List K8s operations

# ✅ Cloud SQL - Lectura
gcloud sql instances list                  # List Cloud SQL instances
gcloud sql instances describe instance-id  # Describe instance
gcloud sql backups list --instance=id      # List backups
gcloud sql operations list                 # List operations

# ✅ Storage - Lectura
gsutil ls                                  # List buckets
gsutil ls -r gs://bucket-name             # List objects in bucket
gsutil ls -r gs://bucket-name/prefix/     # List with prefix
gsutil du gs://bucket-name                # Get bucket size
gsutil cat gs://bucket-name/object        # Read object (stdout)
gcloud storage buckets list                # List buckets (new API)
gcloud storage objects list gs://bucket   # List objects (new API)

# ✅ Cloud Functions - Lectura
gcloud functions list                      # List functions
gcloud functions describe function-name    # Describe function
gcloud functions get-source --name=fn      # Get function source code

# ✅ IAM - Lectura
gcloud iam roles list                                      # List all roles
gcloud iam roles describe roles/compute.instanceAdmin      # Describe role
gcloud projects get-iam-policy project-id                 # Get IAM policy (read)
gcloud compute instances describe instance --format=json  # Get instance IAM

# ✅ Firewall - Lectura
gcloud compute firewall-rules list              # List firewall rules
gcloud compute firewall-rules describe rule-name # Describe rule

# ✅ VPC - Lectura
gcloud compute networks list                    # List networks
gcloud compute networks describe network-name   # Describe network
gcloud compute networks subnets list            # List subnets
gcloud compute addresses list                   # List static IPs
gcloud compute routes list                      # List routes

# ✅ Servicio de credenciales
gcloud auth application-default print-access-token # Get token (read-only context)
gcloud auth application-default login              # Setup only (read)

# ✅ Proyectos
gcloud projects list                           # List projects
gcloud projects describe project-id            # Describe project
gcloud services list --enabled                 # List enabled services

# ✅ Logging
gcloud logging read "resource.type=gce_instance" --limit 50     # Read logs
gcloud logging read --freshness=1h              # Recent logs

# ✅ Monitoring
gcloud monitoring metrics-descriptors list             # List metrics
gcloud monitoring time-series list                     # List time series data

# ✅ DNS
gcloud dns managed-zones list                  # List DNS zones
gcloud dns resource-record-sets list --zone=zone # List records
```

---

### T1: Permitido (Validación Local)

**Patrones permitidos:**

```bash
# ✅ Validación con --dry-run
gcloud compute instances create instance-name --dryrun # Preview creation
gcloud compute firewall-rules create rule-name --dryrun # Preview firewall rule

# ✅ Terraform/Deployment validación
gcloud deployment-manager templates validate --template template.yaml
gcloud container clusters get-credentials --dry-run # Preview credentials fetch

# ✅ Format/lint
gcloud config configurations list               # List configs (non-destructive)
gcloud config configurations describe myconfig  # Describe config

# ✅ Local JSON/YAML validation
python3 -m json.tool config.json               # Validate JSON locally
```

---

### T2: Ask (Requiere Aprobación)

**Patrones que requieren Ask:**

```bash
# ⚠️ Crear - Compute Engine
gcloud compute instances create instance-name --image-family=debian-10 --image-project=debian-cloud
gcloud compute instances create-with-container instance-name --container-image=gcr.io/image

# ⚠️ Crear - Kubernetes
gcloud container clusters create cluster-name --zone=us-central1-a --num-nodes=3
gcloud container node-pools create pool-name --cluster=cluster

# ⚠️ Crear - Cloud SQL
gcloud sql instances create instance-id --database-version=POSTGRES_12 --tier=db-f1-micro
gcloud sql databases create db-name --instance=instance-id

# ⚠️ Crear - Storage
gsutil mb gs://new-bucket-name              # Create bucket
gsutil cp file.txt gs://bucket/file.txt     # Upload file
gcloud storage buckets create gs://bucket   # Create bucket (new API)

# ⚠️ Crear - Firewall rules (no críticas)
gcloud compute firewall-rules create my-rule --allow=tcp:8080 --source-ranges=0.0.0.0/0
gcloud compute firewall-rules create rule --allow tcp:443 --source-ranges=203.0.113.0/24

# ⚠️ Crear - VPC/Subnets
gcloud compute networks create my-network --subnet-mode=custom
gcloud compute networks subnets create my-subnet --network=my-network --range=10.0.0.0/24

# ⚠️ Crear - Cloud Functions
gcloud functions deploy function-name --runtime=python39 --trigger-http
gcloud functions deploy function --source=. --entry-point=handler

# ⚠️ Crear - DNS records
gcloud dns resource-record-sets create example.com --rrdatas=1.2.3.4 --ttl=3600 --type=A

# ⚠️ Modificar - Instancias (reversible)
gcloud compute instances stop instance-name              # Stop instance
gcloud compute instances start instance-name            # Start instance
gcloud compute instances reset instance-name            # Restart instance
gcloud compute instances set-service-account instance-name --service-account=service-account-id

# ⚠️ Modificar - Cloud SQL (reversible)
gcloud sql instances patch instance-id --backup-start-time=03:00 --enable-bin-log
gcloud sql instances restart instance-id

# ⚠️ Modificar - Firewall (reversible)
gcloud compute firewall-rules update rule-name --allow tcp:8080,tcp:443

# ⚠️ Modificar - Storage (no borrar)
gsutil acl set acl.json gs://bucket/object           # Change ACL
gsutil versioning set on gs://bucket                  # Enable versioning
gcloud storage buckets update gs://bucket --versioning  # Enable versioning (new API)

# ⚠️ Modificar - IAM (asignaciones non-críticas)
gcloud projects add-iam-policy-binding project-id --member=user:user@example.com --role=roles/viewer

# ⚠️ Snapshot/Backup (reversible)
gcloud compute snapshots create snapshot-name --source-disk=disk-name --source-disk-zone=zone
gcloud sql backups create --instance=instance-id

# ⚠️ Aplicar cambios reversibles en K8s
gcloud container clusters update cluster --num-nodes=5        # Scale cluster
gcloud container clusters upgrade cluster --master-version 1.23  # Upgrade

# ⚠️ Crear certificados/claves (no secreto maestro)
gcloud compute ssl-certificates create cert-name --certificate=cert.pem --private-key=key.pem
```

---

### T3: Bloqueado (Nunca permitido)

**Patrones explícitamente bloqueados:**

```bash
# ❌ NUNCA - Eliminar Kubernetes clusters (infraestructura crítica)
❌ gcloud container clusters delete cluster-name --zone=zone

# ❌ NUNCA - Eliminar Cloud SQL (data crítica)
❌ gcloud sql instances delete instance-id

# ❌ NUNCA - Eliminar proyectos (infraestructura crítica)
❌ gcloud projects delete project-id

# ❌ NUNCA - Eliminar Compute Engine (infraestructura)
❌ gcloud compute instances delete instance-name
❌ gcloud compute disks delete disk-name
❌ gcloud compute images delete image-name

# ❌ NUNCA - Eliminar VPC/subnets (infraestructura crítica)
❌ gcloud compute networks delete network-name
❌ gcloud compute networks subnets delete subnet-name

# ❌ NUNCA - Eliminar Firewall rules (infraestructura crítica)
❌ gcloud compute firewall-rules delete rule-name

# ❌ NUNCA - Eliminar Storage buckets (data crítica)
❌ gsutil rm -r gs://bucket-name/
❌ gsutil rb gs://bucket-name
❌ gcloud storage buckets delete gs://bucket  # new API

# ❌ NUNCA - Eliminar Cloud Functions
❌ gcloud functions delete function-name

# ❌ NUNCA - Eliminar backups
❌ gcloud sql backups delete backup-id --instance=instance-id

# ❌ NUNCA - Eliminar DNS records
❌ gcloud dns resource-record-sets delete name --zone=zone-name --type=A

# ❌ NUNCA - Eliminar servicios críticos
❌ gcloud services disable compute.googleapis.com
❌ gcloud services disable container.googleapis.com
❌ gcloud services disable sql.googleapis.com

# ❌ NUNCA - Modificar IAM crítico
❌ gcloud projects set-iam-policy project-id policy.json  # if grants admin
❌ gcloud iam service-accounts delete service-account-id

# ❌ NUNCA - Borrar datos
❌ gsutil rm gs://bucket/object                    # Delete object
❌ gsutil rm -r gs://bucket/prefix/                # Delete objects recursively
❌ gcloud storage objects delete gs://bucket/object # Delete (new API)

# ❌ NUNCA - Cambios destructivos en k8s
❌ gcloud container clusters resize cluster --num-nodes=0  # Kill all nodes
```

---

## Docker CLI - Especificación Completa

### T0: Permitido (Read-Only)

**Patrones permitidos:**

```bash
# ✅ Información de contenedores
docker ps                                    # List running containers
docker ps -a                                 # List all containers (including stopped)
docker ps --no-trunc                         # Show full output
docker ps -q                                 # Show only container IDs
docker inspect container-name                # Inspect container
docker inspect container-name --format='{{.State}}' # Inspect specific field
docker exec container-name cat /etc/hostname # Execute read-only command

# ✅ Información de imágenes
docker images                                # List images
docker images --all                          # List all images (including intermediate)
docker images -q                             # Show only image IDs
docker inspect image-name                    # Inspect image
docker history image-name                    # Show image history
docker image inspect image-name              # Inspect image (new syntax)

# ✅ Información de volúmenes y networks
docker volume ls                             # List volumes
docker volume inspect volume-name            # Inspect volume
docker network ls                            # List networks
docker network inspect network-name          # Inspect network

# ✅ Logs y estadísticas
docker logs container-name                   # Show container logs
docker logs -f container-name                # Follow logs (streaming)
docker logs --tail 100 container-name        # Last 100 lines
docker logs -t container-name                # Show timestamps
docker stats                                 # Show resource usage
docker stats container-name                  # Stats for specific container
docker stats --no-stream                     # Single snapshot
docker top container-name                    # Show processes in container

# ✅ Búsqueda e información
docker search image-name                     # Search Docker Hub (metadata only)
docker version                               # Show Docker version
docker info                                  # Show Docker system info
docker system df                             # Show disk usage
docker system events --until=1h              # Show system events (read)

# ✅ Configuración y contexto
docker context ls                            # List Docker contexts
docker context inspect context-name          # Inspect context
docker config ls                             # List configs (for swarm)
docker secret ls                             # List secrets (metadata, not content)

# ✅ Diagnostics
docker health check                          # Check health (if configured)
docker run --rm alpine echo "test"           # Validation run (will be cleaned automatically)
```

---

### T1: Permitido (Validación Local)

**Patrones permitidos:**

```bash
# ✅ Validación sin ejecutar
docker build --dry-run=client -f Dockerfile . # Preview build (if supported)

# ✅ Sintaxis validation
docker compose config --quiet                # Validate docker-compose.yml
docker compose ps --quiet                    # Check compose status (non-destructive)

# ✅ Cambios locales reversibles
git add .dockerignore                        # Local staging
```

---

### T2: Ask (Requiere Aprobación)

**Patrones que requieren Ask:**

```bash
# ⚠️ Crear/Construir imágenes
docker build -t image-name:tag -f Dockerfile .
docker build -t image-name:latest --target production .
docker commit container-name image-name:tag  # Create image from container
docker compose build                         # Build services

# ⚠️ Correr contenedores (no críticos)
docker run -it alpine sh
docker run -d --name container-name image:tag
docker run --rm alpine echo "test"           # Run and remove
docker run -e VAR=value image-name           # With environment variables
docker run -v /local/path:/container/path image-name  # With volume mount

# ⚠️ Modificar contenedores (reversible)
docker start container-name                  # Start stopped container
docker stop container-name                   # Stop running container
docker restart container-name                # Restart container
docker pause container-name                  # Pause container
docker unpause container-name                # Unpause container
docker rename container-name new-name        # Rename container

# ⚠️ Copiar archivos (reversible)
docker cp file.txt container-name:/path/     # Copy to container
docker cp container-name:/path/file.txt .    # Copy from container

# ⚠️ Modificar imágenes (no destructivo)
docker tag source-image:tag target-image:tag # Tag image
docker image tag source:tag target:tag       # Tag image (new syntax)

# ⚠️ Modificar volúmenes (reversible)
docker volume create volume-name             # Create volume
docker volume create -d local volume-name    # Create volume with driver

# ⚠️ Modificar networks (reversible)
docker network create my-network             # Create network
docker network create --driver bridge my-network
docker network connect network-name container-name  # Connect container
docker network disconnect network-name container-name # Disconnect (reversible if needed)

# ⚠️ Push imágenes (reversible)
docker push image-name:tag                   # Push to registry
docker pull image-name:tag                   # Pull from registry
docker image push image:tag                  # Push (new syntax)

# ⚠️ Compose operations (reversible)
docker compose up -d                         # Start services
docker compose down                          # Stop services (NO DESTROY volumes by default)
docker compose stop                          # Stop without removing
docker compose start                         # Start stopped services
docker compose restart service-name          # Restart service

# ⚠️ Exportar/Importar (reversible)
docker save image-name:tag > image.tar       # Export image
docker load < image.tar                      # Import image
docker export container-name > container.tar # Export container
docker import container.tar image-name:tag   # Import container

# ⚠️ Crear snapshots
docker commit container-name image-name:snapshot # Create image snapshot
```

---

### T3: Bloqueado (Nunca permitido)

**Patrones explícitamente bloqueados:**

```bash
# ❌ NUNCA - Eliminar contenedores
❌ docker rm container-name                      # Remove container
❌ docker rm -f container-name                   # Force remove
❌ docker container rm container-name
❌ docker rm $(docker ps -a -q)                  # Remove all containers
❌ docker prune --all --force                    # Prune all containers/images/volumes

# ❌ NUNCA - Eliminar imágenes
❌ docker rmi image-name:tag                     # Remove image
❌ docker rmi -f image-name:tag                  # Force remove image
❌ docker image rm image-name
❌ docker rmi $(docker images -q)                # Remove all images
❌ docker rmi $(docker images --filter "dangling=true" -q)  # Remove dangling

# ❌ NUNCA - Eliminar volúmenes
❌ docker volume rm volume-name                  # Remove volume
❌ docker volume rm $(docker volume ls -q)       # Remove all volumes
❌ docker volume prune --force                   # Prune unused volumes

# ❌ NUNCA - Eliminar networks
❌ docker network rm network-name                # Remove network
❌ docker network prune --force                  # Prune unused networks

# ❌ NUNCA - Limpiar agresivo
❌ docker system prune --all --force --volumes  # Nuke everything
❌ docker container prune --force                # Remove all stopped containers
❌ docker image prune --force --all              # Remove all unused images

# ❌ NUNCA - Eliminar con docker compose
❌ docker compose down --volumes                 # Destroy volumes!
❌ docker compose down --remove-orphans          # If stops everything
❌ docker compose rm                             # Remove services

# ❌ NUNCA - Operaciones destructivas en Swarm
❌ docker service rm service-name                # Remove service
❌ docker stack rm stack-name                    # Remove stack

# ❌ NUNCA - Cambios persistentes en imágenes
❌ docker save image-name:old > image.tar && docker rmi image-name:old  # Hide image
```

---

## Patrones de Decisión Unificados

### Matriz Universal: Cómo Clasificar Nuevos Comandos

Cuando veas un comando nuevo, aplica este árbol de decisión:

```
┌─ Paso 1: ¿Es lectura pura (describe, list, get, show, logs, status, config)?
│  └─ SÍ → T0 (PERMITIDO)
│
├─ Paso 2: ¿Es validación o dry-run (--dryrun, validate, lint, format --check)?
│  └─ SÍ → T1 (PERMITIDO)
│
├─ Paso 3: ¿Es cambio local reversible (git add, local file edits, local commits)?
│  └─ SÍ → T1 (PERMITIDO)
│
├─ Paso 4: ¿Crea/modifica recurso no-crítico (pod, service, VM pequeña, bucket regular)?
│  ├─ ¿Puedo deshacerlo fácilmente? (delete, recreate)
│  │  └─ SÍ → T2 (ASK)
│  └─ ¿Es irreversible o costoso de deshacer?
│     └─ SÍ → T3 (BLOQUEADO)
│
├─ Paso 5: ¿Crea/modifica recurso CRÍTICO (cluster, proyecto, firewall, database)?
│  └─ SÍ → T3 (BLOQUEADO) - Demasiado riesgo, necesita contexto y planning
│
├─ Paso 6: ¿Contiene "delete", "destroy", "drop", "truncate", "rm -rf"?
│  ├─ ¿Es crítico? (cluster, project, database, VPC, firewall)
│  │  └─ SÍ → T3 (BLOQUEADO)
│  └─ ¿Es no-crítico? (pod, service, image, file)
│     └─ ASK → T2 (Requiere confirmación)
│
└─ Paso 7: ¿Usa flags peligrosos (--force, -f, --recursive sin límite)?
   └─ SÍ → T3 (BLOQUEADO) - A menos que sea lectura (curl -f)
```

### Clasificación Rápida por Verbo

| Verbo | Ejemplo | Clasificación | Notas |
|-------|---------|---|---|
| describe, list, get, show | `aws ec2 describe-instances` | T0 | Siempre lectura |
| status, config, version, logs | `kubectl logs pod` | T0 | Siempre lectura |
| validate, lint, format --check | `terraform validate` | T1 | Validación local |
| plan, show, dry-run | `terraform plan` | T1 | Simulación sin aplicar |
| create | `kubectl create namespace` | T2 | Requiere Ask |
| apply, patch, update | `kubectl patch pod` | T2 | Requiere Ask |
| install, upgrade, rollback | `helm upgrade release` | T2 | Requiere Ask |
| start, stop, restart, pause | `docker stop container` | T2 | Reversible, requiere Ask |
| delete (no-crítico) | `kubectl delete pod` | T2 | Requiere Ask |
| delete (crítico) | `gcloud container clusters delete` | T3 | BLOQUEADO |
| destroy | `terraform destroy` | T3 | BLOQUEADO |
| rm, rmi, prune (agregado) | `docker image prune --all` | T3 | BLOQUEADO |

---

## Matriz de Decisión Rápida

### Por Nube + Recurso

| Plataforma | Recurso | Operación | T0 | T1 | T2 | T3 |
|---|---|---|---|---|---|---|
| **AWS** | EC2 Instance | describe | ✅ | | | |
| | | start/stop | | | ✅ | |
| | | run/terminate | | | | ❌ |
| | RDS Database | describe | ✅ | | | |
| | | create | | | ✅ | |
| | | delete | | | | ❌ |
| | S3 Bucket | list | ✅ | | | |
| | | create | | | ✅ | |
| | | rm (delete objects) | | | ❌ | |
| | | rb (delete bucket) | | | | ❌ |
| | IAM Role | list | ✅ | | | |
| | | create | | | | ❌ |
| | | delete | | | | ❌ |
| **GCP** | Compute Instance | describe | ✅ | | | |
| | | create | | | ✅ | |
| | | delete | | | | ❌ |
| | GKE Cluster | describe | ✅ | | | |
| | | create | | | ✅ | |
| | | delete | | | | ❌ |
| | Cloud SQL | describe | ✅ | | | |
| | | create | | | ✅ | |
| | | delete | | | | ❌ |
| | Firewall Rule | describe | ✅ | | | |
| | | create | | | ✅ | |
| | | delete | | | | ❌ |
| **Docker** | Container | ps (list) | ✅ | | | |
| | | run | | | ✅ | |
| | | rm (remove) | | | | ❌ |
| | Image | images (list) | ✅ | | | |
| | | build | | | ✅ | |
| | | rmi (remove) | | | | ❌ |
| | Volume | ls (list) | ✅ | | | |
| | | create | | | ✅ | |
| | | rm (remove) | | | | ❌ |

---

## Ejemplos Testables

### Suite de Prueba 1: AWS CLI

```bash
# T0 - Debe permitirse automáticamente
aws s3 ls                                 # ✅
aws ec2 describe-instances               # ✅
aws iam list-users                        # ✅

# T1 - Debe permitirse (validación)
aws ec2 run-instances --dryrun ...        # ✅

# T2 - Debe pedir confirmación
aws s3 mb s3://my-new-bucket              # ⚠️ Ask
aws ec2 run-instances --image-id ami-xxx  # ⚠️ Ask

# T3 - Debe bloquearse
aws ec2 terminate-instances --instance-ids i-xxx   # ❌ BLOCKED
aws s3 rb s3://bucket-name                         # ❌ BLOCKED
aws iam delete-role --role-name role               # ❌ BLOCKED
```

### Suite de Prueba 2: GCP gcloud

```bash
# T0 - Debe permitirse
gcloud compute instances list              # ✅
gcloud container clusters list             # ✅
gcloud storage buckets list                # ✅

# T1 - Debe permitirse (validación)
gcloud compute instances create --dryrun   # ✅

# T2 - Debe pedir confirmación
gcloud compute instances create instance-name  # ⚠️ Ask
gsutil mb gs://my-new-bucket                   # ⚠️ Ask

# T3 - Debe bloquearse
gcloud compute instances delete instance-name       # ❌ BLOCKED
gcloud container clusters delete cluster-name      # ❌ BLOCKED
gsutil rm -r gs://bucket-name/                     # ❌ BLOCKED
```

### Suite de Prueba 3: Docker CLI

```bash
# T0 - Debe permitirse
docker ps                                  # ✅
docker images                              # ✅
docker logs container-name                 # ✅

# T1 - Debe permitirse (validación)
docker compose config                      # ✅

# T2 - Debe pedir confirmación
docker run -it alpine sh                   # ⚠️ Ask
docker build -t image:tag .                # ⚠️ Ask

# T3 - Debe bloquearse
docker rm container-name                   # ❌ BLOCKED
docker rmi image-name                      # ❌ BLOCKED
docker volume rm volume-name               # ❌ BLOCKED
docker system prune --all --force          # ❌ BLOCKED
```

---

## Patrones de Bloqueo: Regex para .claude/settings.json

### Sección `permissions.deny`

```json
{
  "deny": [
    // AWS - Eliminar crítico
    "Bash(aws ec2 terminate-instances:*)",
    "Bash(aws ec2 delete.*:*)",
    "Bash(aws rds delete-db-instance:*)",
    "Bash(aws rds delete-db-cluster:*)",
    "Bash(aws s3.*rb :*)",
    "Bash(aws s3 rm.*--recursive:*)",
    "Bash(aws iam delete-:*)",
    "Bash(aws iam.*delete.*policy:*)",
    "Bash(aws cloudformation delete-stack:*)",
    "Bash(aws lambda delete-function:*)",
    
    // GCP - Eliminar crítico
    "Bash(gcloud compute instances delete:*)",
    "Bash(gcloud container clusters delete:*)",
    "Bash(gcloud sql instances delete:*)",
    "Bash(gcloud projects delete:*)",
    "Bash(gsutil rb:*)",
    "Bash(gsutil rm.*-r:*)",
    "Bash(gcloud compute firewall-rules delete:*)",
    "Bash(gcloud compute networks delete:*)",
    
    // Docker - Eliminar
    "Bash(docker rm:*)",
    "Bash(docker rmi:*)",
    "Bash(docker volume rm:*)",
    "Bash(docker network rm:*)",
    "Bash(docker system prune.*--all:*)",
    "Bash(docker container prune:*)",
    "Bash(docker image prune.*--all:*)",
    "Bash(docker compose down.*--volumes:*)",
    
    // Kubernetes - Drenaje
    "Bash(kubectl drain:*)",
    
    // Destructivos generales
    "Bash(dd if=:*)",
    "Bash(mkfs.*:*)",
    "Bash(fdisk:*)",
    "Bash(rm -rf /:*)"
  ]
}
```

---

## Configuración de .claude/settings.json Actualizada

### Sección `permissions.allow` (T0 + T1)

```json
{
  "allow": [
    // Herramientas generales Claude Code
    "Read",
    "Glob",
    "Grep",
    "Task",
    "Bash(ls:*)",
    "Bash(pwd:*)",
    "Bash(cd:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(find:*)",
    "Bash(which:*)",
    
    // AWS - T0 (Lectura)
    "Bash(aws s3 ls:*)",
    "Bash(aws s3api get-:*)",
    "Bash(aws s3api list-:*)",
    "Bash(aws s3api head-object:*)",
    "Bash(aws ec2 describe-:*)",
    "Bash(aws ec2 get-:*)",
    "Bash(aws rds describe-:*)",
    "Bash(aws rds list-:*)",
    "Bash(aws iam list-:*)",
    "Bash(aws iam get-:*)",
    "Bash(aws lambda list-functions:*)",
    "Bash(aws lambda get-function:*)",
    "Bash(aws cloudformation describe-:*)",
    "Bash(aws cloudformation get-template:*)",
    "Bash(aws cloudwatch describe-:*)",
    "Bash(aws cloudwatch get-metric:*)",
    "Bash(aws cloudwatch list-:*)",
    "Bash(aws logs describe-:*)",
    "Bash(aws logs get-log-events:*)",
    "Bash(aws sts get-caller-identity:*)",
    
    // AWS - T1 (Validación)
    "Bash(aws.*--dryrun:*)",
    "Bash(aws cloudformation validate-template:*)",
    
    // GCP - T0 (Lectura)
    "Bash(gcloud config list:*)",
    "Bash(gcloud config get-value:*)",
    "Bash(gcloud auth list:*)",
    "Bash(gcloud version:*)",
    "Bash(gcloud compute instances list:*)",
    "Bash(gcloud compute instances describe:*)",
    "Bash(gcloud compute.*describe:*)",
    "Bash(gcloud compute.*list:*)",
    "Bash(gcloud container clusters list:*)",
    "Bash(gcloud container clusters describe:*)",
    "Bash(gcloud sql instances list:*)",
    "Bash(gcloud sql instances describe:*)",
    "Bash(gcloud sql.*list:*)",
    "Bash(gcloud sql.*describe:*)",
    "Bash(gsutil ls:*)",
    "Bash(gcloud storage.*list:*)",
    "Bash(gcloud storage.*describe:*)",
    "Bash(gcloud iam.*list:*)",
    "Bash(gcloud iam.*describe:*)",
    "Bash(gcloud projects list:*)",
    "Bash(gcloud projects describe:*)",
    "Bash(gcloud logging read:*)",
    
    // GCP - T1 (Validación)
    "Bash(gcloud.*--dryrun:*)",
    
    // Docker - T0 (Lectura)
    "Bash(docker ps:*)",
    "Bash(docker images:*)",
    "Bash(docker inspect:*)",
    "Bash(docker history:*)",
    "Bash(docker logs:*)",
    "Bash(docker stats:*)",
    "Bash(docker top:*)",
    "Bash(docker volume ls:*)",
    "Bash(docker network ls:*)",
    "Bash(docker search:*)",
    "Bash(docker version:*)",
    "Bash(docker info:*)",
    "Bash(docker system df:*)",
    
    // Docker - T1 (Validación)
    "Bash(docker compose config:*)",
    
    // Kubernetes - T0 (Ya existentes)
    "Bash(kubectl get:*)",
    "Bash(kubectl describe:*)",
    "Bash(kubectl logs:*)",
    "Bash(kubectl explain:*)",
    "Bash(kubectl version:*)",
    "Bash(kubectl config:*)",
    "Bash(kubectl top:*)",
    "Bash(kubectl wait:*)",
    
    // Git - T0
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git show:*)",
    "Bash(git branch:*)",
    
    // Terraform - T0
    "Bash(terraform version:*)",
    "Bash(terraform fmt:*)",
    "Bash(terraform validate:*)",
    "Bash(terraform show:*)",
    "Bash(terraform output:*)"
  ]
}
```

### Sección `permissions.ask` (T2)

```json
{
  "ask": [
    "Edit",
    "Write",
    "NotebookEdit",
    
    // AWS - T2 (Crear/Modificar)
    "Bash(aws s3 mb:*)",
    "Bash(aws s3 cp:*)",
    "Bash(aws s3api put-:*)",
    "Bash(aws ec2 run-instances:*)",
    "Bash(aws ec2 create-:*)",
    "Bash(aws ec2 modify-:*)",
    "Bash(aws ec2 start-instances:*)",
    "Bash(aws ec2 stop-instances:*)",
    "Bash(aws ec2 reboot-instances:*)",
    "Bash(aws ec2 authorize-security-group:*)",
    "Bash(aws rds create-:*)",
    "Bash(aws rds modify-:*)",
    "Bash(aws rds restore-:*)",
    "Bash(aws iam create-:*)",
    "Bash(aws iam attach-:*)",
    "Bash(aws iam put-:*)",
    "Bash(aws lambda create-function:*)",
    "Bash(aws lambda update-:*)",
    "Bash(aws cloudformation create-stack:*)",
    "Bash(aws cloudformation update-stack:*)",
    
    // GCP - T2 (Crear/Modificar)
    "Bash(gcloud compute instances create:*)",
    "Bash(gcloud compute instances stop:*)",
    "Bash(gcloud compute instances start:*)",
    "Bash(gcloud compute instances reset:*)",
    "Bash(gcloud compute.*create:*)",
    "Bash(gcloud compute.*update:*)",
    "Bash(gcloud container clusters create:*)",
    "Bash(gcloud container clusters update:*)",
    "Bash(gcloud container node-pools create:*)",
    "Bash(gcloud sql instances create:*)",
    "Bash(gcloud sql instances patch:*)",
    "Bash(gcloud sql databases create:*)",
    "Bash(gsutil mb:*)",
    "Bash(gsutil cp:*)",
    "Bash(gcloud storage buckets create:*)",
    "Bash(gcloud storage objects copy:*)",
    "Bash(gcloud functions deploy:*)",
    
    // Docker - T2 (Crear/Modificar)
    "Bash(docker build:*)",
    "Bash(docker run:*)",
    "Bash(docker commit:*)",
    "Bash(docker start:*)",
    "Bash(docker stop:*)",
    "Bash(docker restart:*)",
    "Bash(docker pause:*)",
    "Bash(docker unpause:*)",
    "Bash(docker rename:*)",
    "Bash(docker cp:*)",
    "Bash(docker tag:*)",
    "Bash(docker push:*)",
    "Bash(docker pull:*)",
    "Bash(docker volume create:*)",
    "Bash(docker network create:*)",
    "Bash(docker network connect:*)",
    "Bash(docker compose up:*)",
    "Bash(docker compose down:*)",
    "Bash(docker compose stop:*)",
    "Bash(docker compose start:*)",
    "Bash(docker compose restart:*)",
    
    // Kubernetes - T2 (Ya existentes)
    "Bash(kubectl delete:*)",
    "Bash(kubectl rollout:*)",
    "Bash(kubectl scale:*)",
    "Bash(kubectl patch:*)",
    "Bash(kubectl create:*)",
    "Bash(kubectl apply:*)",
    "Bash(kubectl replace:*)",
    "Bash(kubectl exec:*)",
    
    // Flux - T2
    "Bash(flux delete:*)",
    "Bash(flux reconcile:*)",
    "Bash(flux create:*)",
    "Bash(flux suspend:*)",
    "Bash(flux resume:*)",
    
    // Helm - T2
    "Bash(helm install:*)",
    "Bash(helm upgrade:*)",
    "Bash(helm uninstall:*)",
    "Bash(helm delete:*)",
    "Bash(helm rollback:*)",
    
    // Git - T2
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git merge:*)",
    "Bash(git rebase:*)",
    "Bash(git cherry-pick:*)",
    "Bash(git add:*)",
    
    // Terraform - T2
    "Bash(terraform plan:*)",
    "Bash(terraform apply:*)",
    "Bash(terragrunt plan:*)",
    "Bash(terragrunt apply:*)",
    
    // Sistema archivos - T2
    "Bash(rm:*)",
    "Bash(rmdir:*)",
    "Bash(mv:*)",
    "Bash(cp:*)",
    "Bash(chmod:*)",
    "Bash(chown:*)"
  ]
}
```

---

## Notas de Implementación

### 1. Validar Configuración

```bash
# Validar JSON está bien formado
python3 -m json.tool .claude/settings.json > /dev/null && echo "Valid"

# Contar reglas por tier
jq '.permissions.allow | length' .claude/settings.json   # T0+T1
jq '.permissions.ask | length' .claude/settings.json      # T2
jq '.permissions.deny | length' .claude/settings.json     # T3
```

### 2. Testing Automático

```bash
# Ejecutar suite de pruebas
python3 -m pytest .claude/tests/permissions-validation/ -v

# Probar routing específico
python3 .claude/tools/agent_router.py --json "aws s3 ls"     # Debe permitir
python3 .claude/tools/agent_router.py --json "aws s3 rb bucket"  # Debe denegar
```

### 3. Actualizar Documentación

- Actualizar `.claude/config/agent-catalog.md` con nuevas reglas
- Agregar ejemplos a `.claude/tests/permissions-validation/manual-permission-validation.md`
- Vincular a documentación oficial (AWS, GCP, Docker)

---

**Fin de Especificación Exhaustiva**
