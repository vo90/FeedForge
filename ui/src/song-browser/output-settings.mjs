export function outputSettingsKey(settings) {
  return settings ? JSON.stringify({
    ...(typeof settings.outputDir === 'string' && settings.outputDir ? { outputDir: settings.outputDir } : {}),
    outputLayout: settings.outputLayout || 'flat', nameTemplate: settings.nameTemplate ?? '{source}',
    generateDifficulty: settings.generateDifficulty === true,
  }) : '';
}
