/**
 * DALIOS Mobile App — React Native Entry Point
 *
 * Bottom tab navigation matching the web app structure.
 * Shares the same API client (mobile/src/lib/api.js).
 *
 * Setup:
 *   npx react-native init DaliosMobile --template react-native-template-typescript
 *   Copy mobile/src/* into the generated project
 *   npm install @react-navigation/native @react-navigation/bottom-tabs
 *   npm install react-native-screens react-native-safe-area-context
 */

import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { StatusBar } from 'react-native';

import DashboardScreen from './src/screens/DashboardScreen';
import PortfolioScreen from './src/screens/PortfolioScreen';
import SignalsScreen from './src/screens/SignalsScreen';

const Tab = createBottomTabNavigator();

export default function App() {
  return (
    <NavigationContainer
      theme={{
        dark: true,
        colors: {
          primary: '#ff8c00',
          background: '#050508',
          card: '#0a0e14',
          text: '#e8e8e8',
          border: 'rgba(255,255,255,0.06)',
          notification: '#ff1744',
        },
      }}
    >
      <StatusBar barStyle="light-content" backgroundColor="#050508" />
      <Tab.Navigator
        screenOptions={{
          tabBarStyle: {
            backgroundColor: '#0a0e14',
            borderTopColor: 'rgba(255,255,255,0.06)',
            height: 60,
            paddingBottom: 8,
          },
          tabBarActiveTintColor: '#ff8c00',
          tabBarInactiveTintColor: '#5a6474',
          tabBarLabelStyle: { fontSize: 10, fontWeight: '600', letterSpacing: 0.5 },
          headerStyle: { backgroundColor: '#0a0e14', elevation: 0, shadowOpacity: 0 },
          headerTintColor: '#e8e8e8',
          headerTitleStyle: { fontWeight: '700', letterSpacing: 1 },
        }}
      >
        <Tab.Screen name="Dashboard" component={DashboardScreen} />
        <Tab.Screen name="Portfolio" component={PortfolioScreen} />
        <Tab.Screen name="Signals" component={SignalsScreen} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
