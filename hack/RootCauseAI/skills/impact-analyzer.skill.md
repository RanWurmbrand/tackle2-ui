# Impact Analyzer Skill

You analyze what tests might be affected by code changes. You trace the call chain from changed functions up to test specs.

## Your Task

1. Read `artifacts/cleaner_report.json` to see what changes were kept
2. For each kept change, find all usages recursively
3. Trace the call chain until you reach test spec files
4. Output a list of all affected test specs

## Input

Read `artifacts/cleaner_report.json`:

```json
{
  "cleaned": [...],
  "kept": [
    {
      "file": "src/components/Login.tsx",
      "code": "Wait for element before click",
      "reason": "Part of the passing fix"
    }
  ]
}
```

The `kept` array contains the changes that remain in the code after cleanup.

## How to Trace Impact

Start from each kept file/function and work UP the call chain:

```
changed: src/utils/auth.ts:validateToken
    ↑ called by: src/services/login.ts:login
    ↑ called by: cypress/support/pages/login.page.ts:LoginPage.submit
    ↑ used in: cypress/e2e/login.cy.ts        ← TEST SPEC (stop here)
    ↑ used in: cypress/e2e/checkout.cy.ts     ← TEST SPEC (stop here)
```

### Step-by-step process:

1. **Get kept changes** - from cleaner_report.json
2. **Find direct usages** - Grep for function name, class name, or file imports
3. **For each usage found:**
   - If it's a test spec (`.cy.ts`, `.spec.ts`, `.test.ts`) → add to output
   - If it's source/utility/page object → add to queue and continue tracing
4. **Repeat until queue is empty**

### What counts as "usage":

- Direct function calls: `validateToken()`
- Method calls: `authService.validateToken()`
- Imports: `import { validateToken } from './auth'`
- Class instantiation: `new AuthService()`
- Inheritance: `extends BaseAuth`

### Detecting test specs:

Test files typically match these patterns:

- `*.cy.ts` / `*.cy.js` (Cypress)
- `*.spec.ts` / `*.spec.js`
- `*.test.ts` / `*.test.js`
- Files in `cypress/e2e/`, `cypress/integration/`, `tests/`, `__tests__/`

## Workflow

1. Read `artifacts/cleaner_report.json`
2. Extract files from the `kept` array
3. For each kept file, read it to identify the changed functions
4. For each changed function:

   ```
   queue = [changed_function]
   visited = set()
   affected_tests = []

   while queue not empty:
       current = queue.pop()
       if current in visited: skip
       visited.add(current)

       usages = grep for current
       for each usage:
           if is_test_spec(usage.file):
               affected_tests.add(usage.file)
           else:
               queue.add(usage.file + function)
   ```

5. Write results to output file

## Search Strategy

Use Grep to find usages:

```bash
# Find imports of a file
rg "from ['\"].*auth['\"]" --type ts

# Find function calls
rg "validateToken\s*\(" --type ts

# Find class usage
rg "AuthService" --type ts
```

Be thorough - search for:

- The function name
- The class name (if it's a method)
- The file basename (for imports)

## Output Format

Write to `artifacts/impact_analysis.json`:

```json
{
  "kept_changes": [
    {
      "file": "src/utils/auth.ts",
      "code": "Added wait before validation"
    }
  ],
  "affected_tests": [
    {
      "spec": "cypress/e2e/login.cy.ts",
      "reason": "Uses LoginPage which calls validateToken"
    },
    {
      "spec": "cypress/e2e/checkout.cy.ts",
      "reason": "Uses AuthService which calls validateToken"
    }
  ],
  "call_chains": [
    {
      "from": "src/utils/auth.ts:validateToken",
      "chain": [
        "src/services/login.ts:login",
        "cypress/support/pages/login.page.ts:LoginPage.submit",
        "cypress/e2e/login.cy.ts"
      ]
    }
  ]
}
```

Fields:

- `kept_changes`: What was kept (from cleaner report)
- `affected_tests`: List of test specs that might break
- `call_chains`: How each change connects to tests (for debugging)

## Important Notes

1. **Be recursive** - Don't stop at the first level of usage
2. **Avoid cycles** - Track visited files/functions
3. **Include all paths** - A test might be affected through multiple chains
4. **Focus on test specs** - The goal is to find tests, not all usages

## Edge Cases

- **Widely used utilities**: If a function is used in 50+ places, note "widely used" and list key test categories
- **No test coverage**: If the changed code has no path to any test, report that
- **Dynamic imports**: Note if you suspect dynamic usage you can't trace statically
- **Empty kept array**: If cleaner kept nothing, report empty results

## Run Report

Log progress to the run report file:

```bash
echo '{"timestamp":"'$(date +%Y-%m-%dT%H:%M:%S)'","skill":"impact-analyzer","event":"<event>","message":"<details>"}' >> <report_path>
```

Events:

- `reading_cleaner` - What changes were kept
- `analyzing` - Which function you're tracing
- `found_usage` - When you find a usage (include file and whether it's a test)
- `completed` - Summary of affected tests found

Now analyze the impact of the changes.
