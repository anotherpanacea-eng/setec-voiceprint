### Fixed

- Keep the existing default-profile refusal ordering test host-independent by
  pinning both platform prerequisites only in its consumer module. The actual
  host OS, production platform policy, exact profile refusal and forbidden
  private-root assertion remain unchanged. PATCH.
