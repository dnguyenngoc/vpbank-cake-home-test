#!/bin/bash
set -e

IMAGE_NAME="duynguyenngoc/airflow"
IMAGE_TAG="3.1.7"
FULL_IMAGE_NAME="${IMAGE_NAME}:${IMAGE_TAG}"

echo "Building multi-arch Docker image: ${FULL_IMAGE_NAME}"
echo "Platforms: linux/amd64,linux/arm64"

# Create and use a buildx builder instance if it doesn't exist
BUILDER_NAME="multiarch-builder"
if ! docker buildx inspect "${BUILDER_NAME}" &>/dev/null; then
  echo "Creating buildx builder: ${BUILDER_NAME}"
  docker buildx create --name "${BUILDER_NAME}" --use
  docker buildx inspect --bootstrap
else
  echo "Using existing buildx builder: ${BUILDER_NAME}"
  docker buildx use "${BUILDER_NAME}"
fi

# Build and push multi-arch image
echo "Building and pushing multi-arch image..."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg AIRFLOW_VERSION=3.1.7 \
  -t "${FULL_IMAGE_NAME}" \
  -f Dockerfile \
  --push \
  .

echo "Build and push completed successfully!"
echo "Image: ${FULL_IMAGE_NAME} (supports linux/amd64 and linux/arm64)"
