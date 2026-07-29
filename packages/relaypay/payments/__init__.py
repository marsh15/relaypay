"""Payment lifecycle domain.

Import services from ``relaypay.payments.service`` explicitly. Keeping the
package initializer side-effect free prevents ORM metadata imports from
initializing the service dependency graph during migrations.
"""
