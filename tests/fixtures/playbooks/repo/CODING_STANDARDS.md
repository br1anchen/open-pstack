# Coding standards

Applies to every change in this repository.

## Rust (Engine)

### Errors

Return a typed error from every fallible function. Never `unwrap` outside tests.

Wrap an error with the call that failed before it crosses a module.

### Testing

Test the public interface. A test names the behavior it pins.

## TypeScript (Web)

### Errors

Throw only `AppError` values. Catch at the route boundary.

### Naming

Name a hook for what it returns, not for what it does.
