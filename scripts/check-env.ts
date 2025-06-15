import { existsSync } from 'fs';
import { config } from 'dotenv';
import { z, ZodError } from 'zod';
import { envSchema } from '../env.schema';

const envFile = existsSync('.env') ? '.env' : '.env.example';
config({ path: envFile });

const schema = z.object(envSchema);

try {
  schema.parse(process.env);
  console.log(`✅ ${envFile} validates against env.schema.ts`);
} catch (error) {
  if (error instanceof ZodError) {
    console.error('❌ Invalid or missing environment variables:');
    console.error(error.format());
  } else {
    console.error(error);
  }
  process.exit(1);
}
