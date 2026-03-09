# Validation Assertions: Architecture Review & State Optimization

## Milestone: Component Architecture Review

### VAL-ARCH-001: Component Composition Pattern

**Title:** Components follow composition-based architecture

**Behavioral Description:**
Components use compound component patterns and composition over inheritance. Parent components render children through `children` prop or slots, enabling flexible composition. Components do not deeply nest unrelated concerns.

**Pass Criteria:**
- Components accept `children` prop for composability
- Complex components expose sub-components (e.g., `Parent.Child`, `Parent.Header`)
- No deeply nested component hierarchies exceeding 4 levels
- `data-slot` attributes used for compound component identification

**Fail Criteria:**
- Monolithic components with all UI in single file
- Props used for content that should be children
- Deep prop drilling without context

**Evidence Requirements:**
- Grep for `data-slot` in component files
- Count of components using `children` prop in TypeScript types
- Examples: `Message`/`MessageContent`, `Tool`/`ToolHeader`, `Conversation`/`ConversationContent`

---

### VAL-ARCH-002: TypeScript Strict Typing

**Title:** Components have comprehensive TypeScript type definitions

**Behavioral Description:**
All public component props are typed with explicit interfaces. No use of `any` type. Event handlers typed with proper event types. Generic props use TypeScript generics appropriately.

**Pass Criteria:**
- All exported components have explicit Props interfaces
- Props types extend from appropriate HTML element types (e.g., `HTMLAttributes<HTMLDivElement>`)
- No `any` types in component prop definitions
- Variant props use `VariantProps<typeof cva>` pattern

**Fail Criteria:**
- Use of `any` in public APIs
- Missing type definitions for props
- Inconsistent prop naming across similar components

**Evidence Requirements:**
- Grep for `interface.*Props` in component files
- Check for `any` usage: `grep -r "any" src/components`
- Example: `ButtonVariantProps = VariantProps<typeof buttonVariants>`

---

### VAL-ARCH-003: React Performance Optimization

**Title:** Components use React performance patterns appropriately

**Behavioral Description:**
Expensive components use `memo()` for preventing unnecessary re-renders. Callbacks use `useCallback` when passed to memoized children. Computations use `useMemo` for expensive operations. Stable references maintained across renders.

**Pass Criteria:**
- Components with expensive renders wrapped in `memo()`
- Callbacks passed to child components use `useCallback`
- Custom comparison functions provided when needed (e.g., `MessageResponse`)
- `displayName` assigned to memoized components for debugging

**Fail Criteria:**
- Missing `memo` on components that re-render frequently with same props
- Inline function creation in render for callbacks passed to children
- Missing dependency arrays or incorrect dependencies

**Evidence Requirements:**
- Grep for `memo(` in component files
- Grep for `useCallback` and `useMemo` usage patterns
- Example: `MessageResponse` with custom comparison function

---

### VAL-ARCH-004: shadcn/ui Pattern Compliance

**Title:** shadcn/ui components follow official patterns and conventions

**Behavioral Description:**
UI components in `components/ui/` follow shadcn/ui conventions. Components use `cva` (class-variance-authority) for variant handling. The `cn()` utility used for class merging. Components are registered in `components.json`.

**Pass Criteria:**
- UI components use `cva` for variant definitions
- `cn()` utility used for conditional class merging
- `data-slot` attributes for component identification
- Components match shadcn/ui structure (forwardRef, displayName)

**Fail Criteria:**
- Custom variant systems not using `cva`
- Direct className manipulation without `cn()`
- Missing `forwardRef` on primitive components
- Custom implementations that should use shadcn CLI

**Evidence Requirements:**
- Check `components.json` configuration
- Grep for `cva(` in `components/ui/` directory
- Grep for `forwardRef` in UI components
- Example: `button-variants.ts` using `cva` pattern

---

### VAL-ARCH-005: Barrel Export Structure

**Title:** Components use proper barrel exports for clean imports

**Behavioral Description:**
Component directories have `index.ts` barrel exports. Public API clearly defined through exports. Re-exports use explicit named exports. No circular dependencies in barrel files.

**Pass Criteria:**
- Each component directory has `index.ts` with named exports
- Type exports co-located with component exports
- No default exports from barrel files
- Clear public API surface through exports

**Fail Criteria:**
- Missing barrel exports requiring deep imports
- Circular dependencies between barrel files
- Exporting internal implementation details
- Mixed default/named exports in barrel files

**Evidence Requirements:**
- Check for `index.ts` files in component directories
- Verify export statements follow consistent pattern
- Example: `ai-elements/index.ts` with comprehensive exports

---

## Milestone: Hooks & Store Optimization

### VAL-STATE-001: Hook Dependencies Correctness

**Title:** Custom hooks have complete and correct dependency arrays

**Behavioral Description:**
All `useEffect`, `useCallback`, and `useMemo` hooks have exhaustive dependency arrays. Dependencies are stable when possible. ESLint exhaustive-deps rule satisfied.

**Pass Criteria:**
- All effect hooks include all referenced values in dependency array
- Dependencies listed in consistent order
- Stable refs used to avoid unnecessary re-subscriptions
- No disabled exhaustive-deps rules without documented reason

**Fail Criteria:**
- Missing dependencies in effect hooks
- Stale closures from missing dependencies
- Disabled lint rules without justification
- Dependencies that change every render without memoization

**Evidence Requirements:**
- Grep for `useEffect` and verify dependency arrays
- Check for `// eslint-disable-next-line react-hooks/exhaustive-deps`
- Example: `useStickToBottom` with proper cleanup and dependencies

---

### VAL-STATE-002: Hook Cleanup Pattern

**Title:** Hooks properly clean up side effects

**Behavioral Description:**
Effects with subscriptions, timers, or observers return cleanup functions. Event listeners removed on unmount. ResizeObserver, MutationObserver, and IntersectionObserver disconnected. WebSocket connections closed.

**Pass Criteria:**
- All `useEffect` with subscriptions return cleanup function
- Event listeners removed with same reference
- Observers properly disconnected
- AbortController signals respected for async operations

**Fail Criteria:**
- Missing cleanup for event listeners
- Memory leaks from uncleaned subscriptions
- Zombie child processes or timers
- Orphaned observer connections

**Evidence Requirements:**
- Grep for `return () =>` in effect hooks
- Check observer.disconnect() calls
- Example: `useStickToBottom` with ResizeObserver and MutationObserver cleanup

---

### VAL-STATE-003: Hook Naming Conventions

**Title:** Hooks follow React naming conventions

**Behavioral Description:**
Custom hooks prefixed with `use`. Hook names describe their purpose clearly. Hooks return consistent types (tuple, object, or single value). Related hooks grouped in same file when tightly coupled.

**Pass Criteria:**
- All custom hooks start with `use` prefix
- Hook names are descriptive (e.g., `useStickToBottom`, not `useScroll`)
- Return types consistent within hook family
- Hook files follow `use<Name>.ts` naming

**Fail Criteria:**
- Non-standard naming without `use` prefix
- Vague or misleading hook names
- Inconsistent return value patterns

**Evidence Requirements:**
- List all files in `hooks/` directory
- Verify `use` prefix on exported functions
- Examples: `useAuth`, `useNavigation`, `useIsMobile`, `useTheme`

---

### VAL-STATE-004: Zustand Store Typing

**Title:** Zustand stores have complete TypeScript typing

**Behavioral Description:**
Store interfaces define state and actions separately. No `any` types in store definitions. Action parameter and return types explicit. Store type passed to `create<State>` generic.

**Pass Criteria:**
- Store interface defines state shape and action types
- `create<StoreType>` used with explicit typing
- Actions typed with parameter and return types
- Async actions properly typed with Promise returns

**Fail Criteria:**
- Use of `any` in store definitions
- Missing type for state or actions
- Implicit typing from inference without interface
- Untyped middleware usage

**Evidence Requirements:**
- Check stores for interface definitions
- Verify `create<Interface>` pattern usage
- Examples: `ChatStore`, `ArtifactState` with explicit interfaces

---

### VAL-STATE-005: Zustand Best Practices

**Title:** Zustand stores follow recommended patterns

**Behavioral Description:**
Stores use `set` and `get` for state updates. No direct state mutation. Computed values derived outside store or via selectors. Store actions are self-contained.

**Pass Criteria:**
- State updates use `set()` function
- `get()` used to read current state within actions
- No direct state mutations
- Complex derived state computed in components/selectors

**Fail Criteria:**
- Direct state mutation without `set`
- Storing computed/derived state that can be calculated
- Mixing async logic without proper patterns
- Side effects in store actions (should be in effects)

**Evidence Requirements:**
- Review store implementations for `set()` and `get()` usage
- Check for derived state patterns
- Examples: `chatStore.ts`, `artifactStore.ts` patterns

---

### VAL-STATE-006: No Redundant State

**Title:** State is minimal without redundancy

**Behavioral Description:**
No duplicate state across stores. Derived state not stored unless performance-critical. State normalized to avoid synchronization issues. Loading/error states scoped appropriately.

**Pass Criteria:**
- No duplicate state across different stores
- Derived values computed from source state
- Normalized data structures for collections
- Loading states scoped to specific operations

**Fail Criteria:**
- Same data in multiple stores
- Storing values that can be computed
- Denormalized data requiring sync
- Global loading state for unrelated operations

**Evidence Requirements:**
- Review store state definitions for overlaps
- Check for computed selectors
- Example: `messages` state not duplicated in multiple stores

---

### VAL-STATE-007: Context Pattern Compliance

**Title:** React contexts follow best practices

**Behavioral Description:**
Contexts defined with proper TypeScript typing. Context providers handle undefined case gracefully. Context hooks throw descriptive errors when used outside provider. Context split to prevent unnecessary re-renders.

**Pass Criteria:**
- Context type interface defined
- Default value handles undefined case
- Custom hook provides error for missing provider
- High-frequency updates separated to dedicated context

**Fail Criteria:**
- Context default value creates misleading state
- Missing provider error handling
- Single context for unrelated state causing re-renders
- Context used for frequently changing values

**Evidence Requirements:**
- Check `*-context.ts` files for typing patterns
- Verify error handling for missing provider
- Examples: `MessageBranchContext`, `NavigationContext`, `AuthContext`
