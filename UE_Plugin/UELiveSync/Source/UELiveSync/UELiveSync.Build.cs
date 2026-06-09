// Copyright Epic Games, Inc. All Rights Reserved.

using UnrealBuildTool;

public class UELiveSync : ModuleRules
{
	public UELiveSync(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicIncludePaths.AddRange(
			new string[] {

			}
		);

		PrivateIncludePaths.AddRange(
			new string[] {
			}
		);

		PublicDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Engine",
				"InputCore",

				// Phase 7E: Sequencer + Keyframe Replication
				"LevelSequence",
				"MovieScene",
				"MovieSceneTracks",
			}
		);

		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"CoreUObject",
				"Engine",

				"Slate",
				"SlateCore",

				"Sockets",
				"Networking",

				"Json",
				"JsonUtilities",
				"ProceduralMeshComponent",

				"UnrealEd",
				"AssetTools"
			}
		);

		DynamicallyLoadedModuleNames.AddRange(
			new string[]
			{

			}
		);
	}
}