import { useEffect, useState } from 'react';
import { getProfile, PROFILE_UPDATED_EVENT } from '../lib/profile';

export function useProfile() {
  const [profile, setProfile] = useState(getProfile);
  useEffect(() => {
    const onUpdate = () => setProfile(getProfile());
    window.addEventListener(PROFILE_UPDATED_EVENT, onUpdate);
    return () => window.removeEventListener(PROFILE_UPDATED_EVENT, onUpdate);
  }, []);
  return profile;
}
