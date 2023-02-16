# Filter matrix

Filter a JSON matrix using the output of the "dorny/paths-filter" action.

```yaml
- uses: metaplex-foundation/actions/filter-matrix@v1
  with:
    matrix: '["foo", "bar"]'
    changes: ${{ steps.paths_filter.outputs.changes }}
    prefix: ""
    suffix: ""
```

- Inputs:
  - `matrix`: A JSON array of names. **Required**.
  - `changes`: A JSON array of changed filters from "dorny/paths-filter". **Required**.
  - `prefix`: A prefix to prepend to the name when searching for filters. Defaults to `""`.
  - `suffix`: A suffix to append to the name when searching for filters. Defaults to `""`.
- Outputs:
  - `matrix`: A JSON array of name that have changed.

## Example

The example below uses the "dorny/paths-filter" action to detect changes in the `programs` folder. The output of the "dorny/paths-filter" action is used to filter the `PROGRAMS` matrix which is then passed to the `test` job. That way, only the programs that have changes will be tested.

```yaml
jobs:
  changes:
    name: Detect changes
    runs-on: ubuntu-latest
    outputs:
      programs: ${{ steps.changes.outputs.programs }}
      program_matrix: ${{ steps.filter.outputs.matrix }}
    steps:
      - name: Git checkout
        uses: actions/checkout@v3

      - name: Detect changes
        uses: dorny/paths-filter@v2
        id: changes
        with:
          filters: .github/file-filters.yml

      - name: Filter program matrix
        id: filter
        uses: metaplex-foundation/actions/filter-matrix@v1
        with:
          matrix: ${{ vars.PROGRAMS }}
          changes: ${{ steps.changes.outputs.changes }}
          suffix: _program

  test:
    name: Test programs that have changes
    runs-on: ubuntu-latest
    needs: changes
    if: ${{ needs.changes.outputs.programs == 'true' }}
    strategy:
      matrix:
        program: ${{ fromJson(needs.changes.outputs.program_matrix) }}
```
