pipeline {
  agent {
    kubernetes {
      defaultContainer 'kaniko'
      yaml """
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: kaniko
    image: gcr.io/kaniko-project/executor:v1.24.0-debug
    command: ["/busybox/cat"]
    tty: true
"""
    }
  }

  environment {
    IMAGE_BASE = 'registry-docker-registry.registry.svc.cluster.local:5000/signal-tracker'
  }

  stages {
    stage('Build & push') {
      steps {
        /* 1️⃣  compute short SHA from Jenkins-provided env var */
        script {
          env.GIT_SHA = env.GIT_COMMIT.take(7)
          echo "Building tag: ${env.GIT_SHA}"
        }

        /* 2️⃣  run Kaniko and push two tags */
        container(name: 'kaniko', shell: '/busybox/sh') {
          sh '''
            /kaniko/executor \
              --context=$(pwd) \
              --dockerfile=Dockerfile \
              --destination=$IMAGE_BASE:$GIT_SHA \
              --destination=$IMAGE_BASE:latest \
              --label org.opencontainers.image.revision=$GIT_SHA \
              --insecure --skip-tls-verify
          '''
        }
      }
    }
  }

  post {
    success {
      echo "✅ Pushed $IMAGE_BASE:$GIT_SHA and $IMAGE_BASE:latest"
    }
  }
}
