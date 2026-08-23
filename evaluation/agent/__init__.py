"""Evaluation-only reasoning-model harness.

This package is not part of the Akrasia_Zero application runtime.
Production code under app/ must not import it. The harness may call
production analytics; it must not write to application databases,
expose agent endpoints, or supply hidden ground truth to a model.
"""
