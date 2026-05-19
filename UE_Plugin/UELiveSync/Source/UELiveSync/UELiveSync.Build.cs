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
				"InputCore"
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
				"JsonUtilities"
			}
		);

		DynamicallyLoadedModuleNames.AddRange(
			new string[]
			{

			}
		);
	}
}