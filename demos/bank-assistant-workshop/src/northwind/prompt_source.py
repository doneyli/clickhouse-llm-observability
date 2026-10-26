"""Lesson 03 solution: resolve a workshop prompt, with a visible app fallback."""
def resolve(client, name, fallback, *, label, version=None):
    options = {"version": version} if version is not None else {"label": label}
    return client.get_prompt(name, type="text", cache_ttl_seconds=0,
                             fallback=fallback, **options)
