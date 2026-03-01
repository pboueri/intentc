# Product Specifications


This is a specification for the `intentc` compiler at a high level. The goal is to outline the intent of the product, but some implementation details may differe from generation to generation. 


## Overall Intended Workflow

A developer who makes software does so in an iterative fashion. The workflow is roughly:
1. Define the intent they want to accomplish in this session, which builds on all prior intents and builds if they exist. They define validation rules in a companion file with their intent file to 
2. Build the target from the intent using `intentc`, generating any intermediate intents if need be or using them from a cache
3. Inspecting the output and understanding where their intent went wrong. This could be by reading files, seeing the result of their work, or other means
4. Iteratively refine the build in a REPL like manner. What this entails is `intentc refine` which allows you to write  tweak which modifies the intent and validation file, along with the generated source. This refinement allows you to tweak as you wish. 
5. Once the developer is content their generation looks good they `exit` the `intentc refine` experience, which then cleans and rebuilds the target from this session from scratch. They again inspect the outputs and validate that it looks good, refining as needed, with a clean and build step.
6. Ultimately when they are happy they run `intentc commit` which commits their changes to the code base, with both the intent and validation files along with the generated code. `intentc commit` uses git under the hood to commit both the user's product spec files and all the generated code in seperate commits. Git is used as the version control system

## Interface

The core interface consists of the following commands

- `intentc init` -- initializes the project structure whice creates `product_spec` folder with a starting prompt spec + validation spec + project level spec
- `intentc build {target}` -- build to a target, if no target is specified it builds all unbuilt targets. It assigns a `generation-id` to the current build which is used in `intentc commit`
- `intentc clean {target}` -- cleans the generated files from a given target, invalidating all dependent targets and cleaning them as well. If no target is specified it cleans out everything
- `intentc validate {target}` -- runs the validation of the target and determines whether it passes, and if not generates a concise report of what failed
- `intentc refine` -- Enters into a REPL that refines the generated code from the existing target. The user is allowed to enter short prompts and refinements which then are interpreted by the coding agent to update both the intent and/or validation files AND the generated code, without rebuilding everything from scratch. When `exit` is called the system first commits all the changes with `intentc commit` then `intentc clean {target}` and then `intentc build {target}` again. 
- `intent checkout {target} {generation-id}` this allows you to rollback to a prior state if need be
- `intentc status` -- shows the current status of all targets and what is out of date and when things were generated
- `intenct check {target}` -- ensures that all files can be parsed correctly, and suggests edits if not. 
- `intentc validation list` -- lists all validations that the system supports
- `intentc validation add {target} {validation-type}` -- allows you to add a stub of the validation type to the desired target
- `intentc help` -- displays helpful commands
- `intentc config` -- allows you to set project configuration such which coding agent, which models, in a helpful heads up display and then stores it in `.intentc` at the project root

## Project Files

There is a privileged folder in a project called `intent` which defines the intent of the project. Within here are project files authored by the developer that keeps the pure intent of the user. These can only be modified by the system as part of `intentc refine` otherwise they are read only for the rest of the commands. 

Within the `intent` folder there are three types of files:
- Project files, which are stored at the the first level of `intent` which define the intent of the overall project. They are similar in structure to the atomic feature files, but they are used universally throughout the project and surfaced to the coding agents should they need to reference then. Their file extention are `ic` files and have a markdown format
- Feature folders contain the files:
    - An intent file (ending in `.ic`) that are markdown files that are used to define the intent of the feature. You can declare at the top of the file the dependencies of other features by referencing their folder names like: `Depends On: feature_1, feature_2` creating an implicit DAG
    - A set of validation files (ending in `.icv`) which are structured markdown files that clearly define the desired validation. Each validation file can have multiple independent validations, denoted by the hierarchy of headers. The validation type is specified within the subsection and the parameters are defined there as well.

## Validation
Validation is the most crucial part of the system. It allows a user to constrain the generation to a shape that they desire, progressively adding constraints. The generator should be aware of the validations when generating, unless there's an explicit exclusion defined in the file, which hides it from the generating system. 

Validations are pluggable, meaning more validations can be added without needing to rebuild `intentc` in an extension oriented manner. They are discoverable in a manner that is native to the language ecosystem that `intentc` is created in

The base library of validations that `intentc` ships with are:
- FileCheck -- checks whether a file was generated, and natural language assertions about it
- FolderCheck -- checks whether folder was generated, and natural language assertions about it
- WebCheck -- an optional check if supported by the building agent, a check that uses a browser to validate something works with a natural language script of what to do 
- ProjectCheck -- a check about the whole generated project, in natural language
- CommandLineCheck -- a check that can be run in the cli, usually specifying a command or similar that the validation agent would run and inspect output

## Refinement Phase
The refinement phase is the magic that makes the system usable in a tractable time and cost and user friendly. Refining a target generation-id requires quick back and forth with the user and the agent in order to see their work and sculpt quickly. When the user types `initc refine` they should clearly be dropped into a REPL that's distinguished from a regular cli and they can go back and forth with the system, issuing iterative, small commands that go to the agent which does the command. `initc` manages this dialogue by modifying the intent file associated with the generation-id in question. This should be intelligent, compressive, and distill down what the user is trying to achieve in precise changes that also change the validation file. 

When the user types `exit` they are presented with all the changes to their intent and validation files that were generated in that session. If they accept these changes are added. They can elect to issue a new clean and build cycle that will create a new generation-id for this target. 

## Configuring Build Agents
Build agents should be configurable so that new coding agents can be plugged in without needing to rebuild `intentc`. In order to do so there is a special section in `.intentc` that defines the agents available to use and how they can be used. There is a minimal interface defined on how to specify them and what parameters are available -- for example a claude code agent would be defined with how to invoke it, and what parameters can be passed to it. 

By default `intentc` ships with claude code as its primary agent. It is defined by running `intentc init` which initialized it wihtin `.intentc`


## Tracking State

`git` is a prerequisite for `intentc`, though it should be sufficiently abstracted away that another state management tool can be used in the future. State tracking works as follows:

1. Append only commit log -- the commits are all append only and keep a linear history of generating, though the user can roll back to a commit and begin from there again, erasing subsequent commits
2. changes to the `intent` folder are kept separate from the rest of the whole project, keeping a clean delineation between the build specs and the built files. The commit prefixes should clearly seprate the two with `intent:` as the prefix for intent commits and `generated:` as the prefix for generated commits
3. Each time a target is generated a new `generation-id` is assigned. If multiple targets are built then each one is committed in its own `generation-id`


## Language choice and setup

The language of choice for this project is `go` since it is simple, fast, explicit, and comes with a good base library. The generating tool should follow `go` best practices, and coding best practies. There should be clear interface, abstraction, good code organization reflecting the clear structure of the project intent. There should be a complete test suite using a mock generating agent that allows you to simulate commands and validate things work as intended. When testing git in tests, new git repos should be generated and destroyed, and if it is ever touching the primary project git repo the test should error out before any changes are made. 

Ideally most interfaces are managed as code and shelling out -- except for the coding agent where the interface is explicitly a shell.  It should be shipped as a go package. 