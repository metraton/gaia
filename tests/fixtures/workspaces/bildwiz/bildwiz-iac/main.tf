terraform {
  required_version = ">= 1.0"
}

provider "google" {
  project = "bildwiz"
}

module "vpc" {
  source  = "./modules/vpc"
  version = "1.0.0"
}

resource "google_container_cluster" "primary" {
  name     = "bildwiz-primary"
  location = "us-central1"
}
