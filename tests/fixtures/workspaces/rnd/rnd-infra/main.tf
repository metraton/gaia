provider "aws" {
  region = "us-east-1"
}

module "buckets" {
  source = "./modules/buckets"
}
