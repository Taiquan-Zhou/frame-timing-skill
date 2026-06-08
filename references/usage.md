# Usage Notes

The first implementation pass migrated the tested local timing modules into this standalone project.

Required migration checks:

- remove hard-coded user paths;
- keep package imports relative to `frame_timing_agent`;
- provide demo-frame generation without private data;
- run tests from a fresh project checkout;
- verify health reports on generated demo frames;
- keep analysis artifacts free of private absolute input paths.
